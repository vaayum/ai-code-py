FROM python:3.11-slim

# Install necessary system dependencies (git is required by aicoder)
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory for the aicoder installation
WORKDIR /app

# Copy the aicoder source code
COPY . /app/

# Install the package
RUN pip install --no-cache-dir -e .

# Set a separate workspace directory for the user's code
# This is where users should mount their project volumes
WORKDIR /workspace

# Set the entrypoint to aicoder so the container acts like the CLI
ENTRYPOINT ["aicoder"]

# Default command if none is provided
CMD ["--help"]
