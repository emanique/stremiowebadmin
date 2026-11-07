#!/bin/sh
# Start the direct-only Stremio/WebAdmin stack using the host's current LAN IPv4 address.
#
# Docker Compose cannot execute commands inside .env. Detecting the address must therefore happen
# on the host before Compose evaluates compose.yaml. Shell environment variables take precedence
# over .env, so the detected address is injected without rewriting the tracked configuration file.
#
# Development runs local Docker builds. No GitHub Actions or published images are required.
# Arbitrary Docker Compose commands can still be passed through this launcher.
#
# Usage:
#   sh start.sh                         # unified stack: local build + up -d
#   sh start.sh config                  # inspect the resolved compose configuration
#   sh start.sh ps                      # show stack status
#   sh start.sh up -d --force-recreate  # pass arbitrary compose arguments
#   IPADDRESS=192.168.1.10 sh start.sh   # explicit override
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

_show_help() {
    cat <<'EOF'
Usage: sh start.sh [COMMAND] [ARGS...]

Without arguments, detects the host IPv4 and GPU backend, builds the
local images, and starts the direct-only Stremio stack.

Common commands:
  sh start.sh                 Build and start the stack
  sh start.sh config          Show resolved Compose configuration
  sh start.sh ps              Show stack status
  sh start.sh logs -f         Follow stack logs
  sh start.sh up -d           Pass arguments directly to Docker Compose
  sh start.sh --help          Show this launcher help

Automatic GPU policy:
  GPU_BACKEND=auto            Detect VAAPI/NVIDIA; CPU fallback if unavailable
  VAAPI_DEVICE                Auto-detected from /dev/dri/renderD* when unset

Stremio remains authoritative for COPY vs TRANSCODE and codec selection.
EOF
}

case "${1-}" in
    -h|--help|help)
        _show_help
        exit 0
        ;;
esac

# Prevent Docker silently creating a directory for the status-file bind.
python3 "$ROOT/scripts/ensure-port-status.py" >/dev/null

ENV_FILE="$ROOT/.env"
[ -f "$ENV_FILE" ] || cp "$ROOT/.env.example" "$ENV_FILE"

# Read a single literal KEY=value from .env without sourcing/evaluating the file.
# Shell-exported variables keep highest precedence; .env is the persistent local baseline.
_env_value() {
    key=$1
    fallback=${2-}
    value=$(awk -v key="$key" '
        $0 ~ "^[[:space:]]*" key "=" {
            sub("^[[:space:]]*" key "=", "", $0)
            print
            exit
        }
    ' "$ENV_FILE" 2>/dev/null || true)
    [ -n "$value" ] && printf '%s\n' "$value" || printf '%s\n' "$fallback"
}

_detect_ip() {
    if command -v ip >/dev/null 2>&1; then
        detected=$(ip route get "${IP_DETECT_TARGET:-1.1.1.1}" 2>/dev/null \
            | awk '{for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')
        if [ -n "$detected" ]; then
            printf '%s\n' "$detected"
            return
        fi
    fi
    hostname -I 2>/dev/null \
        | awk '{for (i = 1; i <= NF; i++) if ($i !~ /^127\./ && $i !~ /:/) {print $i; exit}}'
}

_is_ipv4() {
    printf '%s\n' "$1" | awk -F. '
        NF != 4 {bad = 1}
        {
            for (i = 1; i <= 4; i++) {
                if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) bad = 1
            }
        }
        END {exit bad ? 1 : 0}'
}

IPADDRESS_SOURCE=${IPADDRESS_SOURCE:-$(_env_value IPADDRESS_SOURCE auto)}
IPADDRESS_SOURCE=$(printf '%s' "$IPADDRESS_SOURCE" | tr '[:upper:]' '[:lower:]')

if [ -z "${IPADDRESS:-}" ]; then
    case "$IPADDRESS_SOURCE" in
        manual)
            IPADDRESS=$(_env_value IPADDRESS "")
            ;;
        auto|"")
            IPADDRESS=$(_detect_ip || true)
            ;;
        *)
            echo "[start] invalid IPADDRESS_SOURCE='$IPADDRESS_SOURCE' (use auto or manual)." >&2
            exit 1
            ;;
    esac
fi

if [ -z "${IPADDRESS:-}" ] || ! _is_ipv4 "$IPADDRESS"; then
    echo "[start] unable to determine a valid host IPv4 address." >&2
    echo "[start] set it explicitly, e.g. IPADDRESS=192.168.1.244 sh start.sh" >&2
    exit 1
fi

export IPADDRESS

# In auto mode persist the latest detected host address for Compose/WebAdmin
# operations that may be run without this launcher. Manual mode is never rewritten.
if [ "$IPADDRESS_SOURCE" = "auto" ]; then
    # Keep a stable, host-local baseline for standalone Docker Compose.
    # Never rewrite .env when the effective address has not changed, and
    # never overwrite the JSON application preferences stored in /config.
    python3 "$ROOT/scripts/sync-host-env.py" "$ENV_FILE" "$IPADDRESS" >/dev/null
fi

python3 "$ROOT/scripts/sync-host-dns.py" "$ENV_FILE" >/dev/null

COMPOSE_ARGS="-f compose.yaml"

GPU_BACKEND=${GPU_BACKEND:-$(_env_value GPU_BACKEND auto)}
VAAPI_DEVICE=${VAAPI_DEVICE:-$(_env_value VAAPI_DEVICE "")}
LIBVA_DRIVER_NAME=${LIBVA_DRIVER_NAME:-$(_env_value LIBVA_DRIVER_NAME "")}

# Intel Media Driver is case-sensitive on Linux: the installed module is
# iHD_drv_video.so. Canonicalize legacy/inherited lowercase overrides so an
# environment such as LIBVA_DRIVER_NAME=ihd cannot break otherwise working
# Intel VAAPI discovery.
case "$(printf '%s' "$LIBVA_DRIVER_NAME" | tr '[:upper:]' '[:lower:]')" in
    ihd)
        LIBVA_DRIVER_NAME=iHD
        ;;
esac

if [ -n "$LIBVA_DRIVER_NAME" ]; then
    export LIBVA_DRIVER_NAME
else
    # Remove a blank legacy override so libva can auto-detect the runtime driver.
    if grep -q '^[[:space:]]*LIBVA_DRIVER_NAME=[[:space:]]*$' "$ENV_FILE" 2>/dev/null; then
        sed -i '/^[[:space:]]*LIBVA_DRIVER_NAME=[[:space:]]*$/d' "$ENV_FILE"
    fi
    unset LIBVA_DRIVER_NAME
fi
GPU_BACKEND=$(printf '%s' "$GPU_BACKEND" | tr '[:upper:]' '[:lower:]')

_detect_vaapi_device() {
    # Launcher discovery must not depend on host FFmpeg being installed.
    # Runtime capability is verified later inside the server container by
    # WebAdmin's real FFmpeg self-tests.

    # An explicit render node is still validated by PCI vendor.
    if [ -n "${VAAPI_DEVICE:-}" ] && [ -e "${VAAPI_DEVICE:-}" ]; then
        explicit_vendor="/sys/class/drm/$(basename "${VAAPI_DEVICE:-}")/device/vendor"
        if [ ! -r "$explicit_vendor" ] || [ "$(cat "$explicit_vendor")" != "0x10de" ]; then
            printf '%s\n' "${VAAPI_DEVICE:-}"
            return 0
        fi
        echo "[start] ignoring NVIDIA nouveau DRM node for generic VAAPI: ${VAAPI_DEVICE:-}" >&2
    fi

    # Prefer Intel DRM render nodes. Enumerate renderD* rather than assuming
    # renderD128 because Proxmox/LXC and multi-GPU hosts can expose renderD129+
    # instead.
    for dev in /dev/dri/renderD*; do
        [ -e "$dev" ] || continue

        node=$(basename "$dev")
        vendor_file="/sys/class/drm/$node/device/vendor"

        if [ -r "$vendor_file" ] && [ "$(cat "$vendor_file" 2>/dev/null)" = "0x8086" ]; then
            printf '%s\n' "$dev"
            return 0
        fi
    done

    # Avoid classifying an NVIDIA nouveau node as generic VAAPI.
    for dev in /dev/dri/renderD*; do
        [ -e "$dev" ] || continue
        vendor_file="/sys/class/drm/$(basename "$dev")/device/vendor"
        if [ -r "$vendor_file" ] && [ "$(cat "$vendor_file")" = "0x10de" ]; then
            continue
        fi
        printf '%s\n' "$dev"
        return 0
    done

    return 1
}

_detect_vaapi_driver() {
    dev="$1"

    [ -n "$dev" ] || return 1
    [ -e "$dev" ] || return 1

    vendor=""
    driver=""

    drm_name=$(basename "$dev")
    sysdev="/sys/class/drm/$drm_name/device"

    if [ -r "$sysdev/vendor" ]; then
        vendor=$(cat "$sysdev/vendor" 2>/dev/null || true)
    fi

    if [ -e "$sysdev/driver" ]; then
        driver=$(basename "$(readlink -f "$sysdev/driver" 2>/dev/null)" 2>/dev/null || true)
    fi

    # Intel DRM/i915: use the modern Intel Media Driver.
    #
    # Driver availability is validated inside the runtime image, not on the
    # Docker host. The host is responsible only for identifying the GPU.
    #
    # Never automatically fall back to i965. On some Intel generations i965
    # can initialise partially and then abort FFmpeg during encoding.
    if [ "$vendor" = "0x8086" ] || [ "$driver" = "i915" ]; then
        printf '%s\n' "iHD"
        return 0
    fi

    # AMD
    if [ "$vendor" = "0x1002" ] || [ "$driver" = "amdgpu" ]; then
        for path in \
            /usr/lib/x86_64-linux-gnu/dri/radeonsi_drv_video.so \
            /usr/lib64/dri/radeonsi_drv_video.so \
            /usr/lib/dri/radeonsi_drv_video.so
        do
            if [ -e "$path" ]; then
                printf '%s\n' "radeonsi"
                return 0
            fi
        done
    fi

    return 1
}

_nvidia_available() {
    [ -e /dev/nvidia0 ] || return 1
    command -v nvidia-smi >/dev/null 2>&1 || return 1
    nvidia-smi >/dev/null 2>&1 || return 1

    docker info 2>/dev/null |
        awk '/Runtimes:/ {
            for (i=2; i<=NF; i++)
                if ($i == "nvidia") found=1
        }
        END {exit found ? 0 : 1}'
}

VAAPI_DETECTED_DEVICE=$(_detect_vaapi_device 2>/dev/null || true)
VAAPI_DETECTED_DRIVER=""

if [ -n "$VAAPI_DETECTED_DEVICE" ]; then
    VAAPI_DETECTED_DRIVER=$(_detect_vaapi_driver "$VAAPI_DETECTED_DEVICE" 2>/dev/null || true)
fi

# An inherited shell value can override .env without the operator noticing.
# Intel i915/Skylake was validated with iHD; never use legacy i965 in AUTO.
# Do not infer this from renderD128: multi-GPU hosts have variable node order.
if [ "$VAAPI_DETECTED_DRIVER" = "iHD" ] && [ "${LIBVA_DRIVER_NAME:-}" = "i965" ]; then
    echo "[start] overriding inherited i965: Intel DRM device requires iHD" >&2
    LIBVA_DRIVER_NAME=iHD
    export LIBVA_DRIVER_NAME
fi

NVIDIA_DETECTED=false

if _nvidia_available; then
    NVIDIA_DETECTED=true
else
    for vendor_file in /sys/class/drm/renderD*/device/vendor; do
        if [ -r "$vendor_file" ] && [ "$(cat "$vendor_file")" = "0x10de" ]; then
            echo "[start] NVIDIA GPU detected but NVENC unavailable: proprietary driver and Docker runtime required"
            break
        fi
    done
fi

case "$GPU_BACKEND" in
    auto)
        if [ -n "$VAAPI_DETECTED_DEVICE" ] && [ "$NVIDIA_DETECTED" = "true" ]; then
            VAAPI_DEVICE=$VAAPI_DETECTED_DEVICE
            export VAAPI_DEVICE

            if [ -z "${LIBVA_DRIVER_NAME:-}" ] && [ -n "$VAAPI_DETECTED_DRIVER" ]; then
                LIBVA_DRIVER_NAME=$VAAPI_DETECTED_DRIVER
                export LIBVA_DRIVER_NAME
            fi
            COMPOSE_ARGS="$COMPOSE_ARGS -f compose.vaapi.yaml -f compose.gpu.yaml"

            # Dual-GPU host: expose both accelerator families to the server
            # container. AUTO remains the only execution policy; WebAdmin
            # self-tests report capability and do not select a media profile.
            GPU_BACKEND_EFFECTIVE=hybrid

            echo "[start] GPU backend AUTO -> VAAPI + NVIDIA"
            echo "[start] VAAPI render node: $VAAPI_DEVICE"
            echo "[start] NVIDIA runtime: available"

        elif [ -n "$VAAPI_DETECTED_DEVICE" ]; then
            VAAPI_DEVICE=$VAAPI_DETECTED_DEVICE
            export VAAPI_DEVICE

            if [ -z "${LIBVA_DRIVER_NAME:-}" ] && [ -n "$VAAPI_DETECTED_DRIVER" ]; then
                LIBVA_DRIVER_NAME=$VAAPI_DETECTED_DRIVER
                export LIBVA_DRIVER_NAME
            fi
            COMPOSE_ARGS="$COMPOSE_ARGS -f compose.vaapi.yaml"

            GPU_BACKEND_EFFECTIVE=vaapi

            echo "[start] GPU backend AUTO -> VAAPI"
            echo "[start] VAAPI render node: $VAAPI_DEVICE"

        elif [ "$NVIDIA_DETECTED" = "true" ]; then
            COMPOSE_ARGS="$COMPOSE_ARGS -f compose.gpu.yaml"
            GPU_BACKEND_EFFECTIVE=nvidia
            unset LIBVA_DRIVER_NAME
            echo "[start] GPU backend AUTO -> NVIDIA"

        else
            GPU_BACKEND_EFFECTIVE=cpu
            unset LIBVA_DRIVER_NAME
            echo "[start] GPU backend AUTO -> CPU fallback"
        fi
        ;;

    vaapi|intel|intel-vaapi)
        if [ -z "$VAAPI_DETECTED_DEVICE" ]; then
            echo "[start] ERROR: VAAPI requested but no DRM render node was detected." >&2
            exit 1
        fi

        VAAPI_DEVICE=$VAAPI_DETECTED_DEVICE
        export VAAPI_DEVICE

        if [ -z "${LIBVA_DRIVER_NAME:-}" ] && [ -n "$VAAPI_DETECTED_DRIVER" ]; then
            LIBVA_DRIVER_NAME=$VAAPI_DETECTED_DRIVER
            export LIBVA_DRIVER_NAME
        fi
        COMPOSE_ARGS="$COMPOSE_ARGS -f compose.vaapi.yaml"
        GPU_BACKEND_EFFECTIVE=vaapi

        echo "[start] GPU backend forced: VAAPI"
        echo "[start] VAAPI render node: $VAAPI_DEVICE"
        ;;

    nvidia|nvenc)
        if [ "$NVIDIA_DETECTED" != "true" ]; then
            echo "[start] ERROR: NVIDIA requested but GPU/runtime is not available." >&2
            exit 1
        fi

        COMPOSE_ARGS="$COMPOSE_ARGS -f compose.gpu.yaml"
        GPU_BACKEND_EFFECTIVE=nvidia
        unset LIBVA_DRIVER_NAME
        echo "[start] GPU backend forced: NVIDIA"
        ;;

    cpu|software|none)
        GPU_BACKEND_EFFECTIVE=cpu
        unset LIBVA_DRIVER_NAME
        echo "[start] GPU backend forced: CPU"
        ;;

    *)
        echo "[start] ERROR: invalid GPU_BACKEND='$GPU_BACKEND'." >&2
        echo "[start] valid values: auto, vaapi, nvidia, cpu" >&2
        exit 1
        ;;
esac

TRANSCODING_MODE=auto
TRANSCODING_HWACCEL=auto
export TRANSCODING_MODE TRANSCODING_HWACCEL
unset TRANSCODING_VIDEO_CODEC

export GPU_BACKEND GPU_BACKEND_EFFECTIVE

echo "[start] GPU requested : $GPU_BACKEND"
echo "[start] GPU effective : $GPU_BACKEND_EFFECTIVE"

if [ -n "${VAAPI_DEVICE:-}" ]; then
    echo "[start] VAAPI device  : $VAAPI_DEVICE"
fi

if [ -n "${LIBVA_DRIVER_NAME:-}" ]; then
    echo "[start] VAAPI driver  : $LIBVA_DRIVER_NAME"
fi

echo "[start] detected host IPv4: $IPADDRESS"
echo "[start] Web Player : http://$IPADDRESS:8080"
echo "[start] WebAdmin HTTPS: https://$IPADDRESS/"
echo "[start] WebAdmin API  : https://$IPADDRESS/api/status"
echo "[start] API        : http://$IPADDRESS:11470"
echo "[start] Library    : https://$IPADDRESS:12470/library/"

if ! docker compose version >/dev/null 2>&1; then
    echo "[start] Docker Compose plugin is not available." >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    docker compose $COMPOSE_ARGS up -d --build --remove-orphans
    exit 0
fi
# shellcheck disable=SC2086
exec docker compose $COMPOSE_ARGS "$@"
