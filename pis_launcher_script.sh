#!/usr/bin/env bash
# --------------------------------------------------------------------
# PIS SYSTEM SELF-CHECK
# Verifies and repairs Docker, PostgreSQL (pgvector), and RabbitMQ setup.
# --------------------------------------------------------------------

set -e

# --------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------

BACKUP_FILE_NAME="BioData_Dec25_esm2_prott5_prostt5_ankh3_large_esm3c_Layers_3Frist_3Last.backup"

ZENODO_URL="https://zenodo.org/records/17793273/files/${BACKUP_FILE_NAME}?download=1"
BACKUP_FOLDER="/home/alexdoro/PycharmProjects/BACKUPs"
BACKUP_FILE="${BACKUP_FOLDER}/${BACKUP_FILE_NAME}"

DATABASE_NAME="BioData"

REBASE_FROM_ZENODO=false
REBASE_FROM_BACKUP=false
CHECK_SERVICES_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --rebase-from-zenodo)
      REBASE_FROM_ZENODO=true
      ;;
    --rebase-from-backup)
      REBASE_FROM_BACKUP=true
      ;;
    --backup-file-name=*)
      BACKUP_FILE_NAME="${arg#*=}"
      ;;
    --backup-folder=*)
      BACKUP_FOLDER="${arg#*=}"
      ;;
    --zenodo-url=*)
      ZENODO_URL="${arg#*=}"
      ;;
    --database-name=*)
      DATABASE_NAME="${arg#*=}"
      ;;
    --check-services|--check-services-only)
      CHECK_SERVICES_ONLY=true
      ;;
  esac
done

BACKUP_FILE="${BACKUP_FOLDER}/${BACKUP_FILE_NAME}"

if [ "$REBASE_FROM_ZENODO" = true ] && [ "$REBASE_FROM_BACKUP" = true ]; then
  echo "❌ Both --rebase-from-zenodo and --rebase-from-backup were provided."
  echo "   Please choose only one option."
  exit 1
fi

if [ "$CHECK_SERVICES_ONLY" = true ] && { [ "$REBASE_FROM_ZENODO" = true ] || [ "$REBASE_FROM_BACKUP" = true ]; }; then
  echo "❌ --check-services cannot be combined with rebase options."
  echo "   Please run service checks separately."
  exit 1
fi

# Function to check required tools
check_dependencies() {
  local missing_tools=()
  
  for tool in docker nc ss systemctl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      missing_tools+=("$tool")
    fi
  done
  
  # Check Python
  if ! command -v python3 >/dev/null 2>&1; then
    missing_tools+=("python3")
  else
    # Check Python version (should be 3.8+)
    local python_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    local version_check=$(python3 -c "import sys; print(1 if sys.version_info >= (3, 8) else 0)" 2>/dev/null || echo "0")
    
    if [ "$version_check" = "0" ]; then
      echo "⚠️  Python version $python_version detected. Python 3.8+ recommended."
    fi
  fi
  
  # Check pip
  if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "⚠️  pip not available. This may cause Poetry installation issues."
  fi
  
  if [ ${#missing_tools[@]} -gt 0 ]; then
    echo "❌ Missing required tools: ${missing_tools[*]}"
    echo "Please install them and try again."
    echo ""
    echo "Installation suggestions:"
    echo "  • Ubuntu/Debian: sudo apt update && sudo apt install ${missing_tools[*]}"
    echo "  • CentOS/RHEL: sudo yum install ${missing_tools[*]}"
    echo "  • macOS: brew install ${missing_tools[*]}"
    exit 1
  fi
}

echo "🔧 Checking dependencies..."
check_dependencies
echo "✅ All required tools available."

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

# Function to check port availability
check_port() {
  local port=$1
  if ss -tuln | grep -q ":$port "; then
    return 1  # Port is in use
  else
    return 0  # Port is free
  fi
}

# Function to find what's using a port
find_port_user() {
  local port=$1
  echo "🔍 Checking what's using port $port..."
  
  # Check for system PostgreSQL
  if systemctl is-active --quiet postgresql 2>/dev/null; then
    echo "   🟡 System PostgreSQL service is running"
  fi
  
  # Check for other Docker containers using the port
  docker ps --format "table {{.Names}}\t{{.Ports}}" | grep ":$port->" || true
  
  # General port check
  ss -tuln | grep ":$port " || echo "   No listeners found on port $port"
  
  # Process check
  local pids=$(ss -tlnp | grep ":$port " | sed 's/.*pid=\([0-9]*\).*/\1/' | sort -u)
  if [ -n "$pids" ]; then
    echo "   Processes using port $port:"
    for pid in $pids; do
      if [ "$pid" != "" ] && kill -0 "$pid" 2>/dev/null; then
        echo "     PID $pid: $(ps -p "$pid" -o comm= 2>/dev/null || echo 'unknown')"
      fi
    done
  fi
}

# Function to handle port conflicts
resolve_port_conflict() {
  local port=$1
  echo "❌ Port $port is already in use!"
  find_port_user $port
  
  echo ""
  echo "🔧 Resolution options:"
  echo "   1. Stop system PostgreSQL: sudo systemctl stop postgresql"
  echo "   2. Use different port for container: -p 5433:5432"
  echo "   3. Remove conflicting containers: docker stop <container_name>"
  echo ""
  
  read -p "🤔 Would you like to automatically try resolution? (y/N): " -n 1 -r
  echo
  
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Try to stop system PostgreSQL if it's running
    if systemctl is-active --quiet postgresql 2>/dev/null; then
      echo "⚙️  Stopping system PostgreSQL service..."
      if sudo systemctl stop postgresql; then
        echo "✅ System PostgreSQL stopped."
        return 0
      else
        echo "❌ Failed to stop system PostgreSQL."
      fi
    fi
    
    # Try to find and stop conflicting Docker containers
    local conflicting_containers=$(docker ps --format "{{.Names}}" --filter "publish=5432")
    if [ -n "$conflicting_containers" ]; then
      echo "⚙️  Stopping conflicting Docker containers..."
      for container in $conflicting_containers; do
        if [ "$container" != "pgvectorsql" ]; then
          echo "   Stopping $container..."
          docker stop "$container" || echo "   Failed to stop $container"
        fi
      done
    fi
    
    # Check if port is now free
    if check_port 5432; then
      echo "✅ Port 5432 is now available."
      return 0
    else
      echo "❌ Port 5432 is still in use. Manual intervention required."
      return 1
    fi
  else
    echo "⚠️  Manual resolution required. Exiting."
    exit 1
  fi
}

if ! docker ps -a --format '{{.Names}}' | grep -q '^pgvectorsql$'; then
  echo "⚙️  Creating new pgvector container..."
  
  # Check port availability before creating
  if ! check_port 5432; then
    if ! resolve_port_conflict 5432; then
      exit 1
    fi
  fi
  
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
    
    # Check port availability before starting
    if ! check_port 5432; then
      if ! resolve_port_conflict 5432; then
        exit 1
      fi
    fi
    
    if ! docker start pgvectorsql; then
      echo "❌ Failed to start pgvectorsql container."
      echo "🔧 Trying to remove and recreate the container..."
      docker rm pgvectorsql 2>/dev/null || true
      
      docker run -d --name pgvectorsql \
        -e POSTGRES_USER=usuario \
        -e POSTGRES_PASSWORD=clave \
        -e POSTGRES_DB=BioData \
        -p 5432:5432 \
        pgvector/pgvector:pg16
    fi
  else
    echo "✅ pgvector is running."
  fi
fi

# RabbitMQ
echo "🔍 Checking RabbitMQ container..."
if ! docker ps -a --format '{{.Names}}' | grep -q '^rabbitmq$'; then
  echo "⚙️  Creating new RabbitMQ container..."
  
  # Check port availability before creating
  if ! check_port 5672; then
    echo "❌ Port 5672 is already in use!"
    find_port_user 5672
    echo "⚠️  Please resolve the port conflict manually or use a different port."
    exit 1
  fi
  
  if ! check_port 15672; then
    echo "❌ Port 15672 is already in use!"
    find_port_user 15672
    echo "⚠️  Please resolve the port conflict manually or use a different port."
    exit 1
  fi
  
  docker run -d --name rabbitmq \
    -p 5672:5672 \
    -p 15672:15672 \
    rabbitmq:management
else
  STATUS=$(docker inspect -f '{{.State.Status}}' rabbitmq)
  if [ "$STATUS" != "running" ]; then
    echo "⚙️  Restarting RabbitMQ container..."
    
    # Check port availability before starting
    if ! check_port 5672 || ! check_port 15672; then
      echo "❌ Required ports (5672 or 15672) are in use!"
      if ! check_port 5672; then
        find_port_user 5672
      fi
      if ! check_port 15672; then
        find_port_user 15672
      fi
      echo "⚠️  Please resolve the port conflicts manually."
      exit 1
    fi
    
    if ! docker start rabbitmq; then
      echo "❌ Failed to start rabbitmq container."
      echo "🔧 Trying to remove and recreate the container..."
      docker rm rabbitmq 2>/dev/null || true
      
      docker run -d --name rabbitmq \
        -p 5672:5672 \
        -p 15672:15672 \
        rabbitmq:management
    fi
  else
    echo "✅ RabbitMQ is running."
  fi
fi

# Verification
echo "🔎 Checking connectivity..."
sleep 5  # Give containers more time to start

# PostgreSQL connectivity check
if nc -z localhost 5432; then
  echo "✅ PostgreSQL reachable."
else
  echo "❌ PostgreSQL unreachable."
  echo "🔧 Diagnostics:"
  docker ps | grep pgvector || echo "   pgvector container not running"
  docker logs --tail 10 pgvectorsql 2>/dev/null || echo "   No logs available"
fi

# RabbitMQ connectivity check
if nc -z localhost 5672; then
  echo "✅ RabbitMQ reachable."
else
  echo "❌ RabbitMQ unreachable."
  echo "🔧 Diagnostics:"
  docker ps | grep rabbitmq || echo "   rabbitmq container not running"
  docker logs --tail 10 rabbitmq 2>/dev/null || echo "   No logs available"
fi

# Additional health checks
echo "🏥 Container health status:"
for container in pgvectorsql rabbitmq; do
  if docker ps --format "{{.Names}}" | grep -q "^${container}$"; then
    status=$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || echo "no-healthcheck")
    echo "   $container: running (health: $status)"
  else
    echo "   $container: not running"
  fi
done

list_databases() {
  export PGPASSWORD="clave"
  if ! command -v psql >/dev/null 2>&1; then
    echo "❌ psql not found. Unable to list databases."
    return 1
  fi
  psql -h localhost -U usuario -d postgres -c "\\l"
}

if [ "$CHECK_SERVICES_ONLY" = true ]; then
  echo "📋 Listing databases on localhost:5432..."
  list_databases || true
  echo "✅ Service check complete."
  exit 0
fi

# --------------------------------------------------------------------
# Rebase BioData database from Zenodo backup (optional flag)
# --------------------------------------------------------------------

reset_biodata_database() {
  echo "🧹 Dropping existing ${DATABASE_NAME} database (if any) and terminating connections..."

  dropdb -h localhost -U usuario "$DATABASE_NAME" --if-exists 2>/dev/null || true
  psql -h localhost -U usuario -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DATABASE_NAME}' AND pid <> pg_backend_pid();" || true
  dropdb -h localhost -U usuario "$DATABASE_NAME" --if-exists 2>/dev/null || true
  psql -h localhost -U usuario -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DATABASE_NAME}';" || true
  sleep 2
  dropdb -h localhost -U usuario "$DATABASE_NAME" --if-exists 2>/dev/null || true

  echo "🆕 Creating fresh ${DATABASE_NAME} database..."
  createdb -h localhost -U usuario "$DATABASE_NAME"

  echo "➕ Ensuring pgvector extension is enabled..."
  psql -h localhost -U usuario -d "$DATABASE_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector;"
}

restore_biodata_backup() {
  local backup_file="$1"
  echo "🔄 Restoring backup into ${DATABASE_NAME} using pg_restore..."

  local restore_err
  restore_err=$(mktemp)
  if ! pg_restore -h localhost -U usuario -d "$DATABASE_NAME" "$backup_file" 2>"$restore_err"; then
    if grep -q "transaction_timeout" "$restore_err"; then
      echo "⚠️  Detected unsupported transaction_timeout setting. Retrying without it..."
      reset_biodata_database

      local list_file
      list_file=$(mktemp)
      pg_restore -l "$backup_file" | sed '/transaction_timeout/d' > "$list_file"
      if ! pg_restore -h localhost -U usuario -d "$DATABASE_NAME" -L "$list_file" "$backup_file"; then
        echo "❌ pg_restore failed even after filtering transaction_timeout."
        rm -f "$list_file" "$restore_err"
        exit 1
      fi
      rm -f "$list_file" "$restore_err"
    else
      cat "$restore_err"
      rm -f "$restore_err"
      echo "❌ pg_restore failed. Please check the error above."
      exit 1
    fi
  else
    rm -f "$restore_err"
  fi
}

rebase_from_zenodo() {
  local zenodo_url="$ZENODO_URL"
  local backups_dir="$BACKUP_FOLDER"
  local file_name="$BACKUP_FILE_NAME"
  local backup_file="$BACKUP_FILE"

  mkdir -p "$backups_dir"

  if [ -f "$backup_file" ]; then
    echo "📦 Backup file already exists: $backup_file"
    echo "What would you like to do?"
    echo "  1) Keep both (download with a new name)"
    echo "  2) Overwrite existing file"
    echo "  3) Use existing file (skip download)"
    read -r -p "Choose 1/2/3: " backup_choice

    case "$backup_choice" in
      1)
        local ts
        ts=$(date +%Y%m%d_%H%M%S)
        local base_name="${file_name%.*}"
        local ext="${file_name##*.}"
        if [ "$base_name" = "$file_name" ]; then
          backup_file="${backup_file}_${ts}"
        else
          backup_file="${backups_dir}/${base_name}_${ts}.${ext}"
        fi
        echo "⬇️  Downloading as: $backup_file"
        ;;
      2)
        echo "⬇️  Overwriting: $backup_file"
        ;;
      3)
        echo "✅ Using existing file: $backup_file"
        ;;
      *)
        echo "❌ Invalid choice. Aborting."
        exit 1
        ;;
    esac
  fi

  if [ ! -f "$backup_file" ] || [ "$backup_choice" = "1" ] || [ "$backup_choice" = "2" ]; then
    if command -v curl >/dev/null 2>&1; then
      curl -L "$zenodo_url" -o "$backup_file"
    elif command -v wget >/dev/null 2>&1; then
      wget -O "$backup_file" "$zenodo_url"
    else
      echo "❌ Neither curl nor wget is available to download the Zenodo backup."
      exit 1
    fi
  fi

  # Basic sanity check: make sure we didn't just download an HTML/error page
  local min_size=$((100*1024*1024))  # 100 MB
  local actual_size=$(stat -c%s "$backup_file" 2>/dev/null || echo 0)
  if [ "$actual_size" -lt "$min_size" ]; then
    echo "❌ Downloaded file looks too small to be a real PostgreSQL backup: ${actual_size} bytes."
    echo "   This usually means ZENODO_URL is not the direct .backup download link (probably an HTML page)."
    echo "   Open the Zenodo record in a browser, click the specific .backup 'Download' button,"
    echo "   copy that link, and paste it into ZENODO_URL, then rerun this script."
    exit 1
  fi

  echo "⚠️  This will DROP and recreate the ${DATABASE_NAME} database on localhost:5432."
  echo "   Continuing in 5 seconds... (Ctrl+C to cancel)"
  sleep 5

  export PGPASSWORD="clave"

  reset_biodata_database
  restore_biodata_backup "$backup_file"

  echo "✅ ${DATABASE_NAME} database successfully rebased from Zenodo backup."
}

rebase_from_backup() {
  local backup_file="$BACKUP_FILE"

  if [ ! -f "$backup_file" ]; then
    echo "❌ Backup file not found: $backup_file"
    echo "   Use --backup-file-name=... and/or --backup-folder=..."
    exit 1
  fi

  echo "⚠️  This will DROP and recreate the ${DATABASE_NAME} database on localhost:5432."
  echo "   Continuing in 5 seconds... (Ctrl+C to cancel)"
  sleep 5

  export PGPASSWORD="clave"

  reset_biodata_database
  restore_biodata_backup "$backup_file"

  echo "✅ ${DATABASE_NAME} database successfully rebased from local backup."
}

if [ "$REBASE_FROM_ZENODO" = true ]; then
  echo "🧬 Rebasing ${DATABASE_NAME} database from Zenodo backup..."
  rebase_from_zenodo
  echo "🎉 Rebase from Zenodo completed. Skipping PIS initialization."
  exit 0
fi

if [ "$REBASE_FROM_BACKUP" = true ]; then
  echo "🧬 Rebasing ${DATABASE_NAME} database from local backup..."
  rebase_from_backup
  echo "🎉 Rebase from backup completed. Skipping PIS initialization."
  exit 0
fi

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

# Function to diagnose Poetry issues
diagnose_poetry_issues() {
  echo "🔍 Diagnosing Poetry environment issues..."
  
  # Check Python version
  local python_version=$(python3 --version 2>&1 || echo "Python not found")
  echo "   🐍 Python version: $python_version"
  
  # Check Poetry environment
  local poetry_env=$(poetry env info --path 2>/dev/null || echo "No environment")
  echo "   📁 Poetry environment: $poetry_env"
  
  # Check if virtual environment exists and is accessible
  if poetry env info >/dev/null 2>&1; then
    local env_python=$(poetry env info --executable 2>/dev/null || echo "Not accessible")
    echo "   🔧 Environment Python: $env_python"
  fi
  
  # Check for common issues
  if [ ! -w "$(dirname "$(poetry config cache-dir 2>/dev/null || echo "$HOME/.cache/pypoetry")")" ]; then
    echo "   ⚠️  Poetry cache directory may not be writable"
  fi
  
  # Check disk space
  local available_space=$(df . | tail -1 | awk '{print $4}')
  if [ "$available_space" -lt 1000000 ]; then  # Less than ~1GB
    echo "   ⚠️  Low disk space: ${available_space}KB available"
  fi
  
  # Check for lock file issues
  if [ -f "poetry.lock" ]; then
    echo "   📋 poetry.lock exists"
    if ! poetry check --lock >/dev/null 2>&1; then
      echo "   ⚠️  poetry.lock may be corrupted or outdated"
    fi
  else
    echo "   📋 No poetry.lock file (will be created)"
  fi
}

# Function to fix Poetry environment issues
fix_poetry_environment() {
  echo "🔧 Attempting to fix Poetry environment..."
  
  # Remove existing environment if corrupted
  if poetry env info >/dev/null 2>&1; then
    echo "   🗑️  Removing existing Poetry environment..."
    poetry env remove --all 2>/dev/null || true
  fi
  
  # Clear Poetry cache
  echo "   🧹 Clearing Poetry cache..."
  poetry cache clear --all . 2>/dev/null || true
  
  # Remove lock file if it exists and is problematic
  if [ -f "poetry.lock" ] && ! poetry check --lock >/dev/null 2>&1; then
    echo "   🗑️  Removing corrupted poetry.lock..."
    rm -f poetry.lock
  fi
  
  # Check for specific dependency conflicts
  if grep -q "posixpath.*ALLOW_MISSING" ~/.cache/pypoetry/artifacts/*/*.log 2>/dev/null || \
     grep -q "posixpath.*ALLOW_MISSING" /tmp/*.log 2>/dev/null; then
    echo "   🔍 Detected posixpath ALLOW_MISSING error - updating Poetry..."
    # This is often caused by outdated setuptools/poetry
    python3 -m pip install --user --upgrade pip setuptools poetry
  fi
  
  # Create fresh environment with specific Python version
  echo "   🆕 Creating fresh Poetry environment..."
  poetry env use python3
  
  # Update pip, setuptools, wheel in the new environment
  echo "   📦 Updating core packages..."
  poetry run python -m pip install --upgrade pip setuptools wheel
}

# Function to install dependencies with multiple strategies
install_dependencies_robust() {
  local attempt=1
  local max_attempts=4
  
  while [ $attempt -le $max_attempts ]; do
    echo "   📥 Dependency installation attempt $attempt/$max_attempts..."
    
    if [ $attempt -eq 1 ]; then
      # First attempt: normal install
      if poetry install --no-interaction; then
        echo "✅ Dependencies installed successfully."
        return 0
      fi
    elif [ $attempt -eq 2 ]; then
      # Second attempt: update lock file and try again
      echo "   🔄 Updating lock file and retrying..."
      rm -f poetry.lock 2>/dev/null || true
      if poetry lock && poetry install --no-interaction; then
        echo "✅ Dependencies installed successfully."
        return 0
      fi
    elif [ $attempt -eq 3 ]; then
      # Third attempt: verbose install to see what's failing
      echo "   🔍 Verbose installation to identify issues..."
      if poetry install --no-interaction -vvv; then
        echo "✅ Dependencies installed successfully."
        return 0
      fi
    else
      # Fourth attempt: install without dev dependencies
      echo "   🎯 Installing only production dependencies..."
      if poetry install --no-interaction --only main; then
        echo "✅ Production dependencies installed successfully."
        echo "⚠️  Note: Development dependencies were skipped."
        return 0
      fi
    fi
    
    if [ $attempt -lt $max_attempts ]; then
      echo "   ❌ Attempt $attempt failed. Trying fixes..."
      
      # Check for specific error patterns and apply targeted fixes
      if [ $attempt -eq 1 ]; then
        # After first failure, try updating Poetry itself
        echo "   🔧 Updating Poetry and core tools..."
        python3 -m pip install --user --upgrade poetry
      fi
      
      fix_poetry_environment
    fi
    
    ((attempt++))
  done
  
  echo "❌ All dependency installation attempts failed."
  return 1
}

echo "⚙️  Ensuring Poetry environment and dependencies..."

# Set up Python environment
poetry env use python3 >/dev/null 2>&1 || {
  echo "⚠️  Failed to set Python environment. Trying diagnostics..."
  diagnose_poetry_issues
  fix_poetry_environment
}

# Install dependencies with robust error handling
if ! install_dependencies_robust; then
  echo ""
  echo "❌ Poetry dependency installation failed after multiple attempts."
  echo ""
  echo "🔧 Manual troubleshooting steps:"
  echo "   1. Check Python version compatibility in pyproject.toml"
  echo "   2. Try: poetry update"
  echo "   3. Try: poetry install --no-dev"
  echo "   4. Check for conflicting system packages"
  echo "   5. Consider using: poetry install --no-cache"
  echo ""
  diagnose_poetry_issues
  exit 1
fi

# --- Run the initialization ---
echo "🚀 Starting PIS initialization via Poetry..."

# Function to run PIS with better error handling
run_pis_initialization() {
  local attempt=1
  local max_attempts=2
  
  while [ $attempt -le $max_attempts ]; do
    echo "   🎯 PIS initialization attempt $attempt/$max_attempts..."
    
    if poetry run pis; then
      echo "✅ Protein-Information-System initialization successful."
      return 0
    else
      local exit_code=$?
      echo "❌ PIS initialization failed with exit code: $exit_code"
      
      if [ $attempt -lt $max_attempts ]; then
        echo "   🔧 Attempting to refresh dependencies and retry..."
        
        # Check if it's a module import error
        if poetry run python -c "import protein_information_system" 2>/dev/null; then
          echo "   ✅ Main module imports successfully"
        else
          echo "   ❌ Module import issues detected"
          echo "   🔧 Reinstalling in editable mode..."
          poetry install --no-interaction
        fi
        
        # Check if config files exist
        if [ ! -f "protein_information_system/config/config.yaml" ]; then
          echo "   ⚠️  Config file missing - this may be expected for first run"
        fi
        
        sleep 2
      fi
    fi
    
    ((attempt++))
  done
  
  return 1
}

if run_pis_initialization; then
  echo "✅ Protein-Information-System setup completed successfully."
else
  echo ""
  echo "❌ PIS initialization failed after multiple attempts."
  echo ""
  echo "🔧 Troubleshooting steps:"
  echo "   1. Check if all dependencies are properly installed:"
  echo "      poetry run python -c 'import protein_information_system'"
  echo "   2. Verify configuration files exist:"
  echo "      ls -la protein_information_system/config/"
  echo "   3. Run with verbose output:"
  echo "      poetry run python -m protein_information_system --help"
  echo "   4. Check logs in the application directory"
  echo ""
  echo "🔍 Environment info:"
  echo "   Poetry environment: $(poetry env info --path 2>/dev/null || echo 'Not found')"
  echo "   Python executable: $(poetry run which python 2>/dev/null || echo 'Not found')"
  echo ""
  exit 1
fi

echo "🎉 System is ready!"

# Final summary
echo ""
echo "📋 SUMMARY:"
echo "  🐳 Docker: $(docker --version | cut -d' ' -f1-3)"
echo "  🗄️  PostgreSQL (pgvector): $(docker ps --format "{{.Status}}" --filter "name=pgvectorsql" 2>/dev/null || echo "Not running")"
echo "  🐰 RabbitMQ: $(docker ps --format "{{.Status}}" --filter "name=rabbitmq" 2>/dev/null || echo "Not running")"
echo "  🐍 Poetry: $(poetry --version 2>/dev/null || echo "Not available")"
echo ""
echo "🌐 Service URLs:"
echo "  • PostgreSQL: localhost:5432"
echo "  • RabbitMQ Management: http://localhost:15672"
echo "  • RabbitMQ AMQP: localhost:5672"
echo ""
echo "🔑 PostgreSQL Credentials:"
echo "  • User: usuario"
echo "  • Password: clave" 
echo "  • Database: BioData"
