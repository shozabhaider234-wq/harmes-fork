#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME="all-MiniLM-L6-v2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMD_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<EOF
Usage: $(basename "$0") [TAG] [TARGET_DIR]

Download and extract the offline ${MODEL_NAME} ONNX embedding model for memd.

Defaults:
  TAG         model-v1 (or MEMD_MODEL_TAG)
  TARGET_DIR  ${MEMD_ROOT}/models/${MODEL_NAME} (or MEMD_MODEL_DIR)
  REPO        hammaddaftab/sematic-search (override with MEMD_MODEL_REPO)
EOF
    exit 0
fi

TAG="${1:-${MEMD_MODEL_TAG:-model-v1}}"
TARGET_DIR="${2:-${MEMD_MODEL_DIR:-${MEMD_ROOT}/models/${MODEL_NAME}}}"
# Default to the upstream model release repo rather than git origin, because forks
# of hermes-agent generally do not mirror the binary release asset.
REPO_SLUG="${MEMD_MODEL_REPO:-hammaddaftab/sematic-search}"

REQUIRED_FILES=(
    "model.onnx"
    "tokenizer.json"
    "config.json"
    "special_tokens_map.json"
    "tokenizer_config.json"
)

has_all_files() {
    local dir="$1"
    for file in "${REQUIRED_FILES[@]}"; do
        [ -f "${dir}/${file}" ] || return 1
    done
    return 0
}

if has_all_files "${TARGET_DIR}"; then
    echo "✓ Model '${MODEL_NAME}' already exists in ${TARGET_DIR}. Skipping download."
    exit 0
fi

URL="https://github.com/${REPO_SLUG}/releases/download/${TAG}/${MODEL_NAME}.tar.gz"
TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

TMP_ARCHIVE="${TMP_DIR}/${MODEL_NAME}.tar.gz"
STAGE_DIR="${TMP_DIR}/extract"

echo "==> Downloading model '${MODEL_NAME}' (${TAG}) from:"
echo "    ${URL}"

if ! curl -fL --progress-bar -o "${TMP_ARCHIVE}" "${URL}"; then
    echo "" >&2
    echo "Error: Failed to download model asset from ${URL}" >&2
    echo "Verify that release '${TAG}' with asset '${MODEL_NAME}.tar.gz' is published at:" >&2
    echo "https://github.com/${REPO_SLUG}/releases/tag/${TAG}" >&2
    exit 1
fi

echo "==> Extracting into ${TARGET_DIR}..."
mkdir -p "${STAGE_DIR}"
tar -xzf "${TMP_ARCHIVE}" -C "${STAGE_DIR}"

if ! has_all_files "${STAGE_DIR}"; then
    echo "Error: Downloaded archive is missing one or more required model files (${REQUIRED_FILES[*]})." >&2
    exit 1
fi

mkdir -p "${TARGET_DIR}"
for file in "${REQUIRED_FILES[@]}"; do
    mv -f "${STAGE_DIR}/${file}" "${TARGET_DIR}/${file}"
done

echo "✓ Model '${MODEL_NAME}' ready in ${TARGET_DIR}!"
