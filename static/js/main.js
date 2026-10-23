document.addEventListener('DOMContentLoaded', () => {
    const fileInput = document.getElementById('file-input');
    const dropZone = document.getElementById('drop-zone');
    const fileInfo = document.getElementById('file-info');
    const fileName = document.getElementById('file-name');
    const fileMeta = document.getElementById('file-meta');
    const clearBtn = document.getElementById('clear-btn');
    const submitBtn = document.getElementById('submit-btn');
    const form = document.getElementById('upload-form');

    const sampleNoHelmetBtn = document.getElementById('load-sample-nohelmet');
    const sampleHelmetBtn = document.getElementById('load-sample-helmet');

    if (!fileInput) return;

    // Helper: Display selected file
    function showFile(file) {
        const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
        const ext = file.name.split('.').pop().toUpperCase();
        fileName.textContent = file.name;
        if (fileMeta) {
            fileMeta.textContent = `${ext} Format • ${sizeMb} MB • Ready for AI inference`;
        }
        fileInfo.style.display = 'flex';
        dropZone.style.display = 'none';
        submitBtn.disabled = false;
    }

    // Helper: Reset file
    function resetFile() {
        fileInput.value = '';
        fileInfo.style.display = 'none';
        dropZone.style.display = 'flex';
        submitBtn.disabled = true;
    }

    // File input change
    fileInput.addEventListener('change', () => {
        if (fileInput.files && fileInput.files.length) {
            showFile(fileInput.files[0]);
        }
    });

    // Drag and drop events
    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.add('dragover');
        });
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.remove('dragover');
        });
    });

    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        if (dt && dt.files && dt.files.length) {
            fileInput.files = dt.files;
            showFile(dt.files[0]);
        }
    });

    // Clear button
    if (clearBtn) {
        clearBtn.addEventListener('click', (e) => {
            e.preventDefault();
            resetFile();
        });
    }

    // Preset Sample Loader
    async function loadPresetSample(samplePath, sampleDisplayName) {
        try {
            if (submitBtn) submitBtn.disabled = true;
            const res = await fetch(samplePath);
            if (!res.ok) throw new Error('Sample not found');
            const blob = await res.blob();
            const file = new File([blob], sampleDisplayName, { type: 'image/jpeg' });
            
            // Set into fileInput via DataTransfer
            const dt = new DataTransfer();
            dt.items.add(file);
            fileInput.files = dt.files;

            showFile(file);
        } catch (err) {
            console.error('Failed to load preset sample:', err);
        }
    }

    if (sampleNoHelmetBtn) {
        sampleNoHelmetBtn.addEventListener('click', (e) => {
            e.preventDefault();
            loadPresetSample('/static/samples/sample_nohelmet.jpg', 'traffic_rider_no_helmet.jpg');
        });
    }

    if (sampleHelmetBtn) {
        sampleHelmetBtn.addEventListener('click', (e) => {
            e.preventDefault();
            loadPresetSample('/static/samples/sample_helmet.jpg', 'traffic_rider_with_helmet.jpg');
        });
    }

    // Submit state animation
    if (form) {
        form.addEventListener('submit', () => {
            const btnText = submitBtn.querySelector('.btn-text');
            const btnSpinner = submitBtn.querySelector('.btn-spinner');
            if (btnText) btnText.style.display = 'none';
            if (btnSpinner) btnSpinner.style.display = 'inline-flex';
            submitBtn.disabled = true;
            submitBtn.style.opacity = '0.9';
        });
    }

    // Intersection observer for subtle fade-up transitions
    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add('sqs-fade-in');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.1 });

    document.querySelectorAll('.bento-card, .sqs-studio-card, .sqs-data-table tbody tr').forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(16px)';
        el.style.transition = 'opacity 0.5s cubic-bezier(0.16, 1, 0.3, 1), transform 0.5s cubic-bezier(0.16, 1, 0.3, 1)';
        observer.observe(el);
    });

    const style = document.createElement('style');
    style.textContent = '.sqs-fade-in { opacity: 1 !important; transform: translateY(0) !important; }';
    document.head.appendChild(style);
});
