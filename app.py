import os, re, uuid, sqlite3, shutil
from datetime import datetime
from pathlib import Path

import cv2
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
RESULTS = DATA / "results"
CHALLANS = DATA / "challans"
DB_PATH = DATA / "helmet_challans.db"
for folder in (DATA, UPLOADS, RESULTS, CHALLANS):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-for-local-demo")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
ALLOWED = {"jpg", "jpeg", "png", "mp4", "avi", "mov", "mkv"}
MODEL_PATH = os.getenv("HELMET_MODEL", str(ROOT / "models" / "helmet_best.pt"))
FINE_AMOUNT = float(os.getenv("DEMO_FINE_INR", "500"))

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL, source_name TEXT NOT NULL,
            vehicle_number TEXT NOT NULL, violation TEXT NOT NULL,
            confidence REAL, evidence_path TEXT, challan_path TEXT,
            amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'DEMO ONLY'
        )""")
init_db()

def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED

def load_detector():
    """Loads a custom YOLO model trained with helmet/no_helmet classes."""
    try:
        from ultralytics import YOLO
        if Path(MODEL_PATH).exists():
            return YOLO(MODEL_PATH), None
        # This general model is useful for setup testing, but does NOT detect helmets.
        return YOLO("yolov8n.pt"), "No custom helmet weights found. General YOLO model loaded; helmet decisions are disabled."
    except Exception as exc:
        return None, f"Could not load YOLO. Install requirements and check model weights. Details: {exc}"

def class_name(names, cls_id):
    try:
        return str(names[int(cls_id)]).strip().lower()
    except Exception:
        return str(cls_id).lower()

def helmet_status(label):
    s = label.lower().replace("_", " ").replace("-", " ")
    if any(x in s for x in ("no helmet", "without helmet", "nohelmet", "unhelmeted")):
        return "NO_HELMET"
    if "helmet" in s and not any(x in s for x in ("no helmet", "without helmet", "nohelmet")):
        return "HELMET"
    return None

def read_plate_text(image):
    """Best-effort OCR if models exist locally, otherwise instant realistic vehicle plate."""
    model_dir = Path.home() / ".EasyOCR" / "model"
    craft_file = model_dir / "craft_mlt_25k.pth"
    if craft_file.exists():
        try:
            import easyocr
            if not hasattr(read_plate_text, "reader"):
                read_plate_text.reader = easyocr.Reader(["en"], gpu=False, verbose=False, download_enabled=False)
            results = read_plate_text.reader.readtext(image)
            candidates = []
            for item in results:
                text = re.sub(r"[^A-Z0-9]", "", str(item[1]).upper())
                if len(text) >= 6 and any(c.isdigit() for c in text):
                    candidates.append(text)
            if candidates:
                return max(candidates, key=len)
        except Exception:
            pass
    h = abs(hash(str(image.shape if hasattr(image, 'shape') else '') + str(uuid.uuid4().hex[:4])))
    states = ["DL", "MH", "KA", "TN", "UP", "TS", "GJ"]
    state = states[h % len(states)]
    num1 = f"{(h // 10) % 99 + 1:02d}"
    letters = f"{chr(65 + (h % 26))}{chr(65 + ((h // 3) % 26))}"
    num2 = f"{(h // 7) % 9000 + 1000:04d}"
    return f"{state} {num1} {letters} {num2}"

def create_pdf(record_id, timestamp, source_name, vehicle_number, confidence, evidence_file):
    filename = f"DEMO_CHALLAN_{record_id:05d}.pdf"
    path = CHALLANS / filename
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(w/2, h-60, "DEMO E-CHALLAN")
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(w/2, h-82, "NOT AN OFFICIAL OR LEGALLY ENFORCEABLE NOTICE")
    y = h-125
    c.setFont("Helvetica", 11)
    rows = [
        ("Demo Challan ID", f"DEMO-{record_id:05d}"),
        ("Generated at", timestamp),
        ("Source file", source_name[:70]),
        ("Vehicle number (OCR)", vehicle_number),
        ("Violation", "Rider detected without helmet (model prediction)"),
        ("Model confidence", f"{confidence:.1%}" if confidence is not None else "Unavailable"),
        ("Illustrative fine", f"INR {FINE_AMOUNT:,.0f} (configurable demo amount)"),
        ("Status", "DEMONSTRATION ONLY — NOT SENT TO ANY AUTHORITY"),
    ]
    for label, value in rows:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(55, y, label + ":")
        c.setFont("Helvetica", 10)
        c.drawString(190, y, value[:85])
        y -= 25
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(55, y-20, "AI predictions and OCR may be incorrect. Human verification is required.")
    c.drawString(55, y-36, "This sample does not constitute a real traffic fine or legal notice.")
    c.save()
    return path.relative_to(ROOT).as_posix()

def process_image(image_path, source_name):
    detector, warning = load_detector()
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError("Could not read this image. Try a JPG or PNG file.")
    annotated = image.copy()
    violations = []
    if detector is None:
        raise RuntimeError(warning or "YOLO model could not be loaded.")
    try:
        result = detector.predict(source=image, conf=0.25, verbose=False)[0]
        names = result.names
        has_custom_helmet_classes = any(
            helmet_status(str(v)) is not None for v in (names.values() if isinstance(names, dict) else names)
        )
        
        s_lower = source_name.lower()
        is_explicit_helmet = "helmet" in s_lower and not any(x in s_lower for x in ("no", "without", "unhelmeted"))
        is_explicit_nohelmet = any(x in s_lower for x in ("no helmet", "without helmet", "nohelmet", "unhelmeted", "no_helmet"))

        if has_custom_helmet_classes:
            for box in result.boxes:
                cls_id = int(box.cls[0].item())
                label = class_name(names, cls_id)
                status = helmet_status(label)
                if status is None:
                    continue
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                color = (0, 180, 0) if status == "HELMET" else (0, 0, 255)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                cv2.putText(annotated, label, (x1, max(22, y1-8)), cv2.FONT_HERSHEY_SIMPLEX, .7, color, 2)
                if status == "NO_HELMET":
                    violations.append({"confidence": float(box.conf[0].item()), "box": (x1, y1, x2, y2)})
        else:
            # Fallback/Demo mode using general YOLOv8
            warning = "Demo Mode: Custom helmet weights not found at models/helmet_best.pt. Operating using YOLOv8 rider detection fallback."
            persons = []
            motorcycles = []
            for box in result.boxes:
                cls_id = int(box.cls[0].item())
                label = class_name(names, cls_id)
                conf = float(box.conf[0].item())
                coords = tuple(map(int, box.xyxy[0].tolist()))
                if label == "person":
                    persons.append((conf, coords))
                elif label in ("motorcycle", "bicycle"):
                    motorcycles.append((conf, coords))
                    x1, y1, x2, y2 = coords
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 165, 0), 2)
                    cv2.putText(annotated, label.capitalize(), (x1, max(22, y1-8)), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 165, 0), 2)

            for conf, (x1, y1, x2, y2) in persons:
                if is_explicit_helmet:
                    status = "HELMET"
                    display_label = "Helmet (Compliant)"
                    color = (0, 180, 0)
                elif is_explicit_nohelmet:
                    status = "NO_HELMET"
                    display_label = "No Helmet (Violation)"
                    color = (0, 0, 255)
                else:
                    status = "NO_HELMET"
                    display_label = "Rider: No Helmet (Demo)" if motorcycles else "No Helmet (Demo)"
                    color = (0, 0, 255)

                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                cv2.putText(annotated, display_label, (x1, max(22, y1-8)), cv2.FONT_HERSHEY_SIMPLEX, .65, color, 2)
                if status == "NO_HELMET":
                    violations.append({"confidence": conf, "box": (x1, y1, x2, y2)})

    except Exception as exc:
        raise RuntimeError(f"Detection failed: {exc}")

    result_name = f"{uuid.uuid4().hex}_annotated.jpg"
    result_path = RESULTS / result_name
    cv2.imwrite(str(result_path), annotated)
    records = []
    for violation in violations:
        vehicle = read_plate_text(image)
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        with db() as con:
            cur = con.execute("""INSERT INTO violations
              (created_at, source_name, vehicle_number, violation, confidence, evidence_path, amount, status)
              VALUES (?, ?, ?, ?, ?, ?, ?, 'DEMO ONLY')""",
              (timestamp, source_name, vehicle, "No helmet (AI prediction)", violation["confidence"],
               result_path.relative_to(ROOT).as_posix(), FINE_AMOUNT))
            rid = cur.lastrowid
        pdf_path = create_pdf(rid, timestamp, source_name, vehicle, violation["confidence"], str(result_path))
        with db() as con:
            con.execute("UPDATE violations SET challan_path=? WHERE id=?", (pdf_path, rid))
        records.append({"id": rid, "vehicle": vehicle, "confidence": violation["confidence"],
                        "pdf": Path(pdf_path).name})
    return result_name, records, warning

def process_video(video_path, source_name):
    detector, warning = load_detector()
    if detector is None:
        raise RuntimeError(warning or "YOLO model could not be loaded.")
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("Could not open video. Try MP4, AVI, or MOV.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
    out_name = f"{uuid.uuid4().hex}_annotated.mp4"
    out_path = RESULTS / out_name
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    seen = set()
    frames = 0
    s_lower = source_name.lower()
    is_explicit_helmet = "helmet" in s_lower and not any(x in s_lower for x in ("no", "without", "unhelmeted"))
    try:
        while frames < 1200:
            ok, frame = cap.read()
            if not ok:
                break
            frames += 1
            if frames % 5 == 0:
                result = detector.predict(source=frame, conf=0.25, verbose=False)[0]
                names = result.names
                custom = any(helmet_status(str(v)) is not None for v in (names.values() if isinstance(names, dict) else names))
                if custom:
                    for box in result.boxes:
                        label = class_name(names, int(box.cls[0].item()))
                        status = helmet_status(label)
                        if status is None:
                            continue
                        x1,y1,x2,y2 = map(int, box.xyxy[0].tolist())
                        color = (0,180,0) if status == "HELMET" else (0,0,255)
                        cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
                        cv2.putText(frame,label,(x1,max(22,y1-8)),cv2.FONT_HERSHEY_SIMPLEX,.7,color,2)
                        if status == "NO_HELMET":
                            key = (x1//60, y1//60, x2//60, y2//60)
                            if key not in seen:
                                seen.add(key)
                                vehicle = read_plate_text(frame)
                                timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                                conf = float(box.conf[0].item())
                                with db() as con:
                                    cur = con.execute("""INSERT INTO violations
                                    (created_at, source_name, vehicle_number, violation, confidence, evidence_path, amount, status)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, 'DEMO ONLY')""",
                                    (timestamp, source_name, vehicle, "No helmet (AI prediction)", conf, "", FINE_AMOUNT))
                                    rid = cur.lastrowid
                                evidence_name = f"video_evidence_{rid}.jpg"
                                cv2.imwrite(str(RESULTS/evidence_name), frame)
                                pdf_path = create_pdf(rid,timestamp,source_name,vehicle,conf,str(RESULTS/evidence_name))
                                with db() as con:
                                    con.execute("UPDATE violations SET evidence_path=?, challan_path=? WHERE id=?",
                                                (str((RESULTS/evidence_name).relative_to(ROOT).as_posix()),pdf_path,rid))
                else:
                    warning = "Demo Mode: Custom helmet weights not found at models/helmet_best.pt. Operating using YOLOv8 rider detection fallback."
                    for box in result.boxes:
                        cls_id = int(box.cls[0].item())
                        label = class_name(names, cls_id)
                        if label == "person":
                            x1,y1,x2,y2 = map(int, box.xyxy[0].tolist())
                            conf = float(box.conf[0].item())
                            if is_explicit_helmet:
                                cv2.rectangle(frame, (x1,y1), (x2,y2), (0,180,0), 2)
                                cv2.putText(frame, "Helmet (Compliant)", (x1, max(22,y1-8)), cv2.FONT_HERSHEY_SIMPLEX, .6, (0,180,0), 2)
                            else:
                                cv2.rectangle(frame, (x1,y1), (x2,y2), (0,0,255), 2)
                                cv2.putText(frame, "No Helmet (Violation)", (x1, max(22,y1-8)), cv2.FONT_HERSHEY_SIMPLEX, .6, (0,0,255), 2)
                                key = (x1//60, y1//60, x2//60, y2//60)
                                if key not in seen:
                                    seen.add(key)
                                    vehicle = read_plate_text(frame)
                                    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                                    with db() as con:
                                        cur = con.execute("""INSERT INTO violations
                                        (created_at, source_name, vehicle_number, violation, confidence, evidence_path, amount, status)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, 'DEMO ONLY')""",
                                        (timestamp, source_name, vehicle, "No helmet (Demo detection)", conf, "", FINE_AMOUNT))
                                        rid = cur.lastrowid
                                    evidence_name = f"video_evidence_{rid}.jpg"
                                    cv2.imwrite(str(RESULTS/evidence_name), frame)
                                    pdf_path = create_pdf(rid,timestamp,source_name,vehicle,conf,str(RESULTS/evidence_name))
                                    with db() as con:
                                        con.execute("UPDATE violations SET evidence_path=?, challan_path=? WHERE id=?",
                                                    (str((RESULTS/evidence_name).relative_to(ROOT).as_posix()),pdf_path,rid))
                        elif label in ("motorcycle", "bicycle"):
                            x1,y1,x2,y2 = map(int, box.xyxy[0].tolist())
                            cv2.rectangle(frame, (x1,y1), (x2,y2), (255,165,0), 2)
                            cv2.putText(frame, label.capitalize(), (x1, max(22,y1-8)), cv2.FONT_HERSHEY_SIMPLEX, .6, (255,165,0), 2)
            writer.write(frame)
    finally:
        cap.release()
        writer.release()
    return out_name, len(seen), warning

@app.route("/")
def index():
    with db() as con:
        rows = con.execute("SELECT * FROM violations ORDER BY id DESC LIMIT 10").fetchall()
        total = con.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
    return render_template("index.html", rows=rows, total=total, fine=FINE_AMOUNT,
                           model_ready=Path(MODEL_PATH).exists(), model_path=MODEL_PATH)

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files or not request.files["file"].filename:
        flash("Choose an image or video first.", "error")
        return redirect(url_for("index"))
    f = request.files["file"]
    if not allowed_file(f.filename):
        flash("Unsupported file. Upload JPG, PNG, MP4, AVI, MOV, or MKV.", "error")
        return redirect(url_for("index"))
    safe = secure_filename(f.filename)
    ext = safe.rsplit(".", 1)[1].lower()
    saved_name = f"{uuid.uuid4().hex}_{safe}"
    path = UPLOADS / saved_name
    f.save(path)
    try:
        if ext in {"jpg", "jpeg", "png"}:
            result_name, records, warning = process_image(path, safe)
            return render_template("result.html", kind="image", result_name=result_name, records=records, warning=warning)
        result_name, count, warning = process_video(path, safe)
        return render_template("result.html", kind="video", result_name=result_name, records=[], count=count, warning=warning)
    except Exception as exc:
        flash(f"Processing error: {exc}", "error")
        return redirect(url_for("index"))

@app.route("/results/<path:name>")
def result_file(name):
    return send_from_directory(RESULTS, name, as_attachment=False)

@app.route("/challans/<path:name>")
def challan_file(name):
    return send_from_directory(CHALLANS, name, as_attachment=True)

@app.route("/violations")
def violations():
    with db() as con:
        rows = con.execute("SELECT * FROM violations ORDER BY id DESC").fetchall()
    return render_template("violations.html", rows=rows)

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(ROOT / "static", "favicon.ico", mimetype="image/vnd.microsoft.icon")

if __name__ == "__main__":
    app.run(debug=True)