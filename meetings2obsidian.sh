#!/bin/bash

# Meetings2Obsidian - Main wrapper script
# Downloads meeting summaries from Heypocket, Google Meet, and Zoom

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Minimum required Python version
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=12

# Default values
CONFIG_FILE=""
SINCE_DATE=""
DRY_RUN=""
VERBOSE=""

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Function to display help
show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Download meeting summaries from multiple platforms to Obsidian.

OPTIONS:
    --config PATH       Path to configuration file
    --since DATE        Only fetch meetings since date (ISO format: YYYY-MM-DD)
    --dry-run          Show what would be downloaded without saving
    --verbose          Enable verbose logging
    -h, --help         Show this help message

EXAMPLES:
    # Run all syncs with default config
    ./meetings2obsidian.sh

    # Run with custom config
    ./meetings2obsidian.sh --config /path/to/config.yaml

    # Fetch meetings from last week
    ./meetings2obsidian.sh --since 2024-01-18

    # Dry run to see what would be downloaded
    ./meetings2obsidian.sh --dry-run --verbose

EOF
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --since)
            SINCE_DATE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN="--dry-run"
            shift
            ;;
        --verbose)
            VERBOSE="--verbose"
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Build common arguments
COMMON_ARGS=""
[[ -n "$CONFIG_FILE" ]] && COMMON_ARGS="$COMMON_ARGS --config $CONFIG_FILE"
[[ -n "$SINCE_DATE" ]] && COMMON_ARGS="$COMMON_ARGS --since $SINCE_DATE"
[[ -n "$DRY_RUN" ]] && COMMON_ARGS="$COMMON_ARGS $DRY_RUN"
[[ -n "$VERBOSE" ]] && COMMON_ARGS="$COMMON_ARGS $VERBOSE"

# Virtual environment directory (inside project)
VENV_DIR="$SCRIPT_DIR/.venv"

# Find a suitable Python 3.12+ interpreter
find_python() {
    local candidates=("python3" "python3.14" "python3.13" "python3.12" "python")
    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" &> /dev/null; then
            local version
            version=$($cmd -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null) || continue
            local major minor
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [[ "$major" -ge "$MIN_PYTHON_MAJOR" ]] && [[ "$minor" -ge "$MIN_PYTHON_MINOR" ]]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

# Bootstrap or activate virtual environment
setup_venv() {
    local python_cmd
    python_cmd=$(find_python) || {
        print_error "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required but not found"
        print_status "Install Python 3.12+: https://www.python.org/downloads/"
        exit 1
    }

    if [[ ! -d "$VENV_DIR" ]]; then
        print_status "Creating virtual environment (.venv) using $python_cmd..."
        "$python_cmd" -m venv "$VENV_DIR"
        print_success "Virtual environment created"

        # Install dependencies
        print_status "Installing dependencies from requirements.txt..."
        "$VENV_DIR/bin/pip" install --quiet --upgrade pip
        "$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
        print_success "Dependencies installed"

        # Install Playwright browser
        print_status "Installing Playwright Chromium browser..."
        "$VENV_DIR/bin/playwright" install chromium
        print_success "Playwright Chromium installed"
    fi

    # Use the venv Python for all subsequent commands
    PYTHON="$VENV_DIR/bin/python"

    # Verify venv Python works
    if [[ ! -x "$PYTHON" ]]; then
        print_error "Virtual environment is broken. Remove .venv and re-run:"
        print_error "  rm -rf $VENV_DIR"
        exit 1
    fi
}

# Set up virtual environment
setup_venv

PYTHON_VERSION=$($PYTHON -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
print_status "Using Python $PYTHON_VERSION ($PYTHON)"

# Print configuration
print_status "Starting Meetings2Obsidian sync"
[[ -n "$DRY_RUN" ]] && print_warning "Running in DRY RUN mode - no files will be saved"
echo ""

# Track success/failure (using simple variables for Bash 3.2 compatibility)
heypocket_result="unknown"
googlemeet_result="unknown"
zoom_result="unknown"
failed_count=0

# Run Heypocket sync
print_status "Syncing Heypocket meetings..."
if $PYTHON "$SCRIPT_DIR/src/heypocket_sync.py" $COMMON_ARGS; then
    heypocket_result="success"
    print_success "Heypocket sync completed"
else
    heypocket_result="failed"
    failed_count=$((failed_count + 1))
    print_error "Heypocket sync failed"
fi
echo ""

# Run Google Meet sync
print_status "Syncing Google Meet meetings..."
if $PYTHON "$SCRIPT_DIR/src/googlemeet_sync.py" $COMMON_ARGS; then
    googlemeet_result="success"
    print_success "Google Meet sync completed"
else
    googlemeet_result="failed"
    failed_count=$((failed_count + 1))
    print_error "Google Meet sync failed"
fi
echo ""

# Run Zoom sync
print_status "Syncing Zoom meetings..."
if $PYTHON "$SCRIPT_DIR/src/zoom_sync.py" $COMMON_ARGS; then
    zoom_result="success"
    print_success "Zoom sync completed"
else
    zoom_result="failed"
    failed_count=$((failed_count + 1))
    print_error "Zoom sync failed"
fi
echo ""

# Print summary
echo "========================================"
print_status "Sync Summary"
echo "========================================"
if [[ "$heypocket_result" == "success" ]]; then
    print_success "heypocket: ✓"
else
    print_error "heypocket: ✗"
fi
if [[ "$googlemeet_result" == "success" ]]; then
    print_success "googlemeet: ✓"
else
    print_error "googlemeet: ✗"
fi
if [[ "$zoom_result" == "success" ]]; then
    print_success "zoom: ✓"
else
    print_error "zoom: ✗"
fi
echo "========================================"

# Exit with error if any sync failed
if [[ $failed_count -gt 0 ]]; then
    print_warning "Some syncs failed. Check logs for details."
    exit 1
fi

print_success "All syncs completed successfully!"
exit 0
