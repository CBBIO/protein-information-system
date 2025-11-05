#!/usr/bin/env bash
# --------------------------------------------------------------------
# PIS SYSTEM SELF-CHECK
# Verifies and repairs Docker, PostgreSQL (pgvector), and RabbitMQ setup.
# --------------------------------------------------------------------

set -e

echo "🔍 Checking Docker daemon..."
if ! systemctl is-active --quiet docker; then
  echo "⚙️  Starting Docker service..."
  sudo systemctl start docker
fi

if ! docker info > /dev/null 2>&1; then
  echo "❌ Docker not accessible. Adding user '$USER' to docker group..."
  sudo usermod -aG docker "$USER"
  echo "⚙️  Reloading group membership for current shell..."
  SCRIPT_PATH="$(realpath "$0")"
  exec sg docker "bash '$SCRIPT_PATH' $*"
fi

echo "✅ Docker is active."

# PostgreSQL (pgvector)
echo "🔍 Checking pgvector container..."
if ! docker ps -a --format '{{.Names}}' | grep -q '^pgvectorsql$'; then
  echo "⚙️  Creating new pgvector container..."
  docker run -d --name pgvectorsql \
    -e POSTGRES_USER=usuario \
    -e POSTGRES_PASSWORD=clave \
    -e POSTGRES_DB=BioData \
    -p 5432:5432 \
    pgvector/pgvector:pg16
else
  STATUS=$(docker inspect -f '{{.State.Status}}' pgvectorsql)
  if [ "$STATUS" != "running" ]; then
    echo "⚙️  Restarting pgvector container..."
    docker start pgvectorsql
  else
    echo "✅ pgvector is running."
  fi
fi

# RabbitMQ
echo "🔍 Checking RabbitMQ container..."
if ! docker ps -a --format '{{.Names}}' | grep -q '^rabbitmq$'; then
  echo "⚙️  Creating new RabbitMQ container..."
  docker run -d --name rabbitmq \
    -p 5672:5672 \
    -p 15672:15672 \
    rabbitmq:management
else
  STATUS=$(docker inspect -f '{{.State.Status}}' rabbitmq)
  if [ "$STATUS" != "running" ]; then
    echo "⚙️  Restarting RabbitMQ container..."
    docker start rabbitmq
  else
    echo "✅ RabbitMQ is running."
  fi
fi

# Verification
echo "🔎 Checking connectivity..."
sleep 3
nc -z localhost 5432 && echo "✅ PostgreSQL reachable." || echo "❌ PostgreSQL unreachable."
nc -z localhost 5672 && echo "✅ RabbitMQ reachable." || echo "❌ RabbitMQ unreachable."

# --------------------------------------------------------------------
# Protein-Information-System Initialization
# --------------------------------------------------------------------
echo "🚀 Running Protein-Information-System initialization..."

# --- Check if poetry is installed ---
if ! command -v poetry >/dev/null 2>&1; then
  echo "⚙️  Poetry not found. Installing Poetry..."

  # Try pipx first (preferred)
  if command -v pipx >/dev/null 2>&1; then
    echo "📦 Installing via pipx..."
    pipx install poetry
  else
    # Fallback to official installer in user space
    echo "📦 Installing via official installer..."
    curl -sSL https://install.python-poetry.org | python3 -
    export PATH="$HOME/.local/bin:$PATH"
  fi

  # Verify install
  if ! command -v poetry >/dev/null 2>&1; then
    echo "❌ Poetry installation failed. Please install manually."
    exit 1
  fi
  echo "✅ Poetry installed successfully."
fi

# --- Initialize and install dependencies safely ---
if [ ! -f "pyproject.toml" ]; then
  echo "❌ pyproject.toml not found. Are you in the right directory?"
  exit 1
fi

echo "⚙️  Ensuring Poetry environment and dependencies..."
poetry env use python3 >/dev/null 2>&1 || true
poetry install --no-interaction --quiet || {
  echo "❌ Poetry dependency installation failed. Check logs."
  exit 1
}

# --- Run the initialization ---
echo "🚀 Starting PIS initialization via Poetry..."
if poetry run pis; then
  echo "✅ Protein-Information-System initialization successful."
else
  echo "❌ Initialization failed. Trying one retry after dependency refresh..."
  poetry install --no-interaction
  if poetry run pis; then
    echo "✅ Retry successful."
  else
    echo "❌ Retry failed. Please check the logs."
    exit 1
  fi
fi

echo "🎉 System is ready!"
