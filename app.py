import os
from flask import Flask, render_template
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-fallback-key')


@app.route('/')
def index():
    """Serve the landing page."""
    return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True)
