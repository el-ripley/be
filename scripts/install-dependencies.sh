#!/bin/bash

# Script to install all dependencies for ai-agent project
# Python 3.11/3.12, Poetry, Docker, and Docker Compose

set -e

echo "🚀 Starting installation of dependencies for ai-agent project..."

# Update system packages
echo "📦 Updating system packages..."
sudo apt-get update

# Check Python version and install required build tools
echo "🐍 Checking Python version and installing build dependencies..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
echo "Found Python $PYTHON_VERSION"

# Install Python development packages and build tools
sudo apt-get install -y \
    python3-dev \
    python3-venv \
    python3-pip \
    build-essential \
    curl \
    git \
    ca-certificates \
    gnupg \
    lsb-release

# Verify Python version meets requirements (>=3.11,<3.13)
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    echo "⚠️  Python version is too old. Installing Python 3.11 from deadsnakes PPA..."
    sudo apt-get install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update
    sudo apt-get install -y python3.11 python3.11-dev python3.11-venv
    # Use Python 3.11 if available
    if [ -f /usr/bin/python3.11 ]; then
        sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
    fi
elif [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 13 ]; then
    echo "⚠️  Python version is too new. Installing Python 3.12 from deadsnakes PPA..."
    sudo apt-get install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update
    sudo apt-get install -y python3.12 python3.12-dev python3.12-venv
    sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1
else
    echo "✅ Python version $PYTHON_VERSION is compatible (>=3.11,<3.13)"
fi

# Install Poetry
echo "📚 Installing Poetry..."
if ! command -v poetry &> /dev/null; then
    curl -sSL https://install.python-poetry.org | python3 -
    export PATH="$HOME/.local/bin:$PATH"
    
    # Add Poetry to PATH permanently
    if ! grep -q "$HOME/.local/bin" ~/.bashrc; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    fi
else
    echo "Poetry is already installed"
    export PATH="$HOME/.local/bin:$PATH"
fi

# Verify Poetry installation
poetry --version

# Install Docker
echo "🐳 Installing Docker..."
# Remove old versions if any
sudo apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Set up Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add current user to docker group (to run docker without sudo)
sudo usermod -aG docker $USER

# Install Docker Compose standalone (latest version)
echo "🐳 Installing Docker Compose (standalone)..."
DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
sudo curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Verify installations
echo ""
echo "✅ Installation complete! Verifying installations..."
echo ""
echo "Python version:"
python3 --version

echo ""
echo "Poetry version:"
poetry --version

echo ""
echo "Docker version:"
docker --version

echo ""
echo "Docker Compose version:"
docker-compose --version

echo ""
echo "🎉 All dependencies installed successfully!"
echo ""
echo "⚠️  IMPORTANT: Please log out and log back in (or run 'newgrp docker') for Docker group changes to take effect."
echo ""
echo "📝 Next steps:"
echo "   1. cd /var/deploy/ai-agent"
echo "   2. poetry install"
echo "   3. Configure your .env file"
echo "   4. docker-compose -f docker-compose.infra.yml up -d  # Start infrastructure"
echo "   5. docker-compose -f docker-compose.app.yml up -d    # Start application"

