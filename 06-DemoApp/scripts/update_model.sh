#!/usr/bin/env bash
# update_model.sh - refresh both demo apps' bundled ChewNet artifacts.
#
# Run from anywhere. The script copies the latest export from
# 02-DataPipeline/exports/ into the iOS demo and macOS showcase.
#
# After running, rebuild the demo app in Xcode (Cmd+R).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PIPELINE_EXPORTS="$REPO_ROOT/02-DataPipeline/exports"
DEMO_APP_ROOT="$REPO_ROOT/06-DemoApp/ChewSense-Demo/ChewSenseDemo"
SHOWCASE_ROOT="$REPO_ROOT/04-RealWorldTesting/ChewSense-Showcase/Sources/ChewSenseShowcase"

SRC_MLPACKAGE="$PIPELINE_EXPORTS/ChewNet.mlpackage"
SRC_NORM_JSON="$PIPELINE_EXPORTS/chewnet_norm.json"
SRC_NORM_SWIFT="$PIPELINE_EXPORTS/NormalizationConstants.swift"

EXPECTED_MODEL_ID="${CHEWSENSE_EXPECTED_MODEL_ID:-chewnet-20260526-011806-c42976be}"

echo "==> ChewSense demos: model update"
echo "    Source : $PIPELINE_EXPORTS"
echo "    iOS    : $DEMO_APP_ROOT"
echo "    macOS  : $SHOWCASE_ROOT"
echo

if [[ ! -d "$SRC_MLPACKAGE" ]]; then
    echo "ERROR: $SRC_MLPACKAGE not found." >&2
    echo "       Run \`python main.py deploy\` in 02-DataPipeline first." >&2
    exit 1
fi
if [[ ! -f "$SRC_NORM_JSON" ]]; then
    echo "ERROR: $SRC_NORM_JSON not found." >&2
    exit 1
fi
if [[ ! -f "$SRC_NORM_SWIFT" ]]; then
    echo "ERROR: $SRC_NORM_SWIFT not found." >&2
    exit 1
fi

MODEL_ID=$(
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["model_id"])' \
        "$SRC_NORM_JSON"
)
if [[ "$MODEL_ID" != "$EXPECTED_MODEL_ID" ]]; then
    echo "ERROR: expected model $EXPECTED_MODEL_ID, found $MODEL_ID." >&2
    echo "       Override CHEWSENSE_EXPECTED_MODEL_ID only after approving a new deployment." >&2
    exit 1
fi

sync_target() {
    local app_root="$1"
    local package_path="$app_root/Resources/ChewNet.mlpackage"
    local json_path="$app_root/Resources/chewnet_norm.json"
    local swift_path="$app_root/Inference/NormalizationConstants.swift"

    mkdir -p "$app_root/Resources" "$app_root/Inference"
    rm -rf "$package_path"
    cp -R "$SRC_MLPACKAGE" "$package_path"
    cp "$SRC_NORM_JSON" "$json_path"
    cp "$SRC_NORM_SWIFT" "$swift_path"

    cmp -s "$SRC_NORM_JSON" "$json_path"
    cmp -s "$SRC_NORM_SWIFT" "$swift_path"
    diff -qr "$SRC_MLPACKAGE" "$package_path" >/dev/null
    echo "    [ok] synchronized $app_root"
}

sync_target "$DEMO_APP_ROOT"
sync_target "$SHOWCASE_ROOT"

MODEL_SHA=$(shasum -a 256 "$SRC_MLPACKAGE/Data/com.apple.CoreML/model.mlmodel" | awk '{print $1}')
WEIGHTS_SHA=$(shasum -a 256 "$SRC_MLPACKAGE/Data/com.apple.CoreML/weights/weight.bin" | awk '{print $1}')
NORM_SHA=$(shasum -a 256 "$SRC_NORM_JSON" | awk '{print $1}')
echo
echo "    model id        : $MODEL_ID"
echo "    model.mlmodel   sha256: $MODEL_SHA"
echo "    weight.bin      sha256: $WEIGHTS_SHA"
echo "    norm json       sha256: $NORM_SHA"
echo
echo "==> Done. Rebuild both demo apps."
