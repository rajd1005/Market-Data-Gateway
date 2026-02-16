# Base Image
FROM python:3.9-slim

# Set environment variables to prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

# Install System Dependencies & Google Chrome (Direct Install Method)
# We download the .deb file directly to avoid the 'apt-key' errors
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    ca-certificates \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy Requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy All Code
COPY . .

# Start the Gateway
CMD ["python", "gateway.py"]
