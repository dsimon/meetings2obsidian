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

# Conda environment name (change this to match your environment)
CONDA_ENV_NAME="meetings2obsidian"

# Default values
CONFIG_FILE=""
SINCE_DATE=""
DRY_RUN=""
VERBOSE=""
CONDA_ACTIVATED_BY_SCRIPT=false

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

# Function to deactivate conda if we activated it
cleanup_conda() {
    if [[ "$CONDA_ACTIVATED_BY_SCRIPT" == "true" ]]; then
        print_status "Deactivating conda environment..."
        conda deactivate 2>/dev/null || true
    fi
}

# Set up trap to ensure cleanup on exit
trap cleanup_conda EXIT

# Activate conda environment if not already active
activate_conda_env() {
    # Check if already in the correct environment
    if [[ "$CONDA_DEFAULT_ENV" == "$CONDA_ENV_NAME" ]]; then
        print_status "Conda environment '$CONDA_ENV_NAME' already active"
        return 0
    fi

    # Find and source conda.sh to enable conda command in script
    local conda_sh=""
    local conda_paths=(
        "$HOME/anaconda3/etc/profile.d/conda.sh"
        "$HOME/miniconda3/etc/profile.d/conda.sh"
        "$HOME/miniforge3/etc/profile.d/conda.sh"
        "$HOME/mambaforge/etc/profile.d/conda.sh"
        "/opt/anaconda3/etc/profile.d/conda.sh"
        "/opt/miniconda3/etc/profile.d/conda.sh"
        "/usr/local/anaconda3/etc/profile.d/conda.sh"
        "/usr/local/miniconda3/etc/profile.d/conda.sh"
    )

    # Also check CONDA_EXE if set (works when conda is already initialized)
    if [[ -n "$CONDA_EXE" ]]; then
        local conda_base=$(dirname $(dirname "$CONDA_EXE"))
        conda_paths=("$conda_base/etc/profile.d/conda.sh" "${conda_paths[@]}")
    fi

    for path in "${conda_paths[@]}"; do
        if [[ -f "$path" ]]; then
            conda_sh="$path"
            break
        fi
    done

    if [[ -z "$conda_sh" ]]; then
        print_warning "Could not find conda.sh - conda may not be installed or not in a standard location"
        print_warning "Continuing without activating conda environment..."
        return 1
    fi

    # Source conda.sh to enable conda command
    source "$conda_sh"

    # Check if the environment exists
    if ! conda env list | grep -q "^$CONDA_ENV_NAME "; then
        print_error "Conda environment '$CONDA_ENV_NAME' does not exist"
        print_status "Create it with: conda create -n $CONDA_ENV_NAME python=3.12"
        return 1
    fi

    # Activate the environment
    print_status "Activating conda environment '$CONDA_ENV_NAME'..."
    conda activate "$CONDA_ENV_NAME"
    CONDA_ACTIVATED_BY_SCRIPT=true
    print_success "Conda environment activated"
    return 0
}

# Activate conda environment
activate_conda_env || {
    print_warning "Proceeding without conda environment activation"
}

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    print_error "Python 3 is not installed or not in PATH"
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
print_status "Using Python $PYTHON_VERSION"

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
if python3 "$SCRIPT_DIR/src/heypocket_sync.py" $COMMON_ARGS; then
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
if python3 "$SCRIPT_DIR/src/googlemeet_sync.py" $COMMON_ARGS; then
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
if python3 "$SCRIPT_DIR/src/zoom_sync.py" $COMMON_ARGS; then
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
