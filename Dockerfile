FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
COPY pip.config /tmp/pip.config
RUN PIP_CONFIG_FILE=/tmp/pip.config \
    pip install --no-cache-dir --progress-bar off -r requirements.txt && rm -f /tmp/pip.config

# Copy application code
COPY . .

# Create data directory for SQLite persistence
RUN mkdir -p /app/data && \
    chown -R 1001:0 /app && \
    chmod -R g=u /app

USER 1001

# Expose port
EXPOSE 8000

# Set default environment variables
ENV DATABASE_URL=sqlite:////app/data/ocp_inventory.db

# Command to run the application
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
