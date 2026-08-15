FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies (important for cv2, lxml, etc.)
# tesseract-ocr is the actual OCR engine pytesseract wraps — without it every
# extract_text_from_image() call silently returns "" (pytesseract raises
# TesseractNotFoundError, caught and swallowed into an empty string), so image
# extraction has never actually worked in this container until now.
RUN apt-get update && apt-get install -y \
    gcc \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/

RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project
COPY . /app/

# Gunicorn entry
CMD ["gunicorn", "article_extractor.wsgi:application", "--bind", "0.0.0.0:8001"]
