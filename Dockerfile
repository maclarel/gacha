# Use Python 3.11 on Alpine Linux for minimal image size
FROM python:3.11-alpine

# Set working directory
WORKDIR /app

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application file
COPY gacha.py .

# Copy default configuration (can be overridden by volume mount)
COPY config.yaml .

# Create directories for volumes
RUN mkdir -p /app/files /app/rules /app/certs

# Expose ports for HTTP and HTTPS
EXPOSE 80 443

# Run the application
CMD ["python3", "gacha.py"]
