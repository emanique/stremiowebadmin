# Subtitle Translator — GPU inventory independently from Stremio

Status: **private develop, experimental**. Inventory and accelerator decisions
do not change Docker, VAAPI, NVENC, GPU drivers or Core transcoding.

## Why VAAPI is not the translation compute backend

VAAPI accelerates video encoding and decoding. The translation engine uses
neural network inference. A VAAPI `/dev/dri/renderD*` node does **not**
imply CUDA, SYCL, Level Zero or OpenVINO inference capability.

[oneAPI](https://oneapi.io/) is a multiarchitecture specification.
[SYCLomatic](https://github.com/oneapi-src/SYCLomatic) assists developers
in porting CUDA C++ kernels to SYCL; it does **not** translate CUDA binaries,
CTranslate2 Python wheels or pre-trained Argos `.argosmodel` files at runtime.

The current CTranslate2 4.9.0 pip runtime supports Intel-optimized **CPU**
execution and NVIDIA **CUDA GPU** execution. Intel GPU inference requires a
**different validated backend**, such as OpenVINO, with its own model conversion,
GPU driver runtime and inference correctness/performance tests.
Do not label the Intel GPU as accelerated until such tests pass.

References:
- https://opennmt.net/CTranslate2/hardware_support.html
- https://docs.openvino.ai/2026/about-openvino/release-notes-openvino/system-requirements.html
- https://docs.openvino.ai/nightly/get-started/install-openvino/configurations/configurations-intel-gpu.html
- https://www.intel.com/content/www/us/en/developer/tools/oneapi/training/migrate-from-cuda-to-cpp-with-sycl.html

## Independent host inventory

```bash
python3 scripts/subtitle-gpu-inventory.py
python3 scripts/subtitle-gpu-inventory.py --output /var/lib/subtitle-translator/gpu-inventory.json
```

The script runs on the Linux **host**, not in the Stremio container. It reads
`/sys/class/drm/renderD*/device` PCI vendor/driver information, the Docker
container's *declared* device allocation and transcoding environment, and the
optional NVIDIA management query. Output includes candidate GPUs not explicitly
assigned as DRM render nodes to Stremio.

Important: **candidate != available inference GPU**. The script does not
infer actual GPU utilization from a device allocation or a one-time process
snapshot. The separate inference runtime must pass a capability check before
CUDA can be selected. On Intel hardware, an installed and verified OpenVINO
GPU backend will be necessary before choosing Intel GPU.

The existing `subtitle_device` WebAdmin setting supports
`auto` / `cpu` / `cuda`. `auto` prefers a tested CTranslate2 CUDA device
and falls back to CPU on failure. There is intentionally **no**
`intel-gpu` option yet, since no Intel GPU inference engine has been
converted and verified. The model remains local and read-only.

## Observed inventory — stremiosrv (2026-11-12)

| Hardware | Render node | Driver | Actual Stremio Docker assignment |
|---|---|---|---|
| Intel HD Graphics 520 (0x8086:1916) | /dev/dri/renderD129 | i915 | Assigned; `VAAPI_DEVICE=/dev/dri/renderD129` |
| NVIDIA GeForce 920M (0x10de:1299) | /dev/dri/renderD128 | nvidia 470.256.02 | Not listed as a Docker DRM device |

The container also sets `NVIDIA_VISIBLE_DEVICES=all`, which is a configuration
intent, not proof of an available CUDA runtime. No NVIDIA compute process was
observed at this snapshot.

**Compatibility gate:** the existing NVIDIA driver 470 is below CUDA 12.x's
minimum 525.60.13 for minor-version compatibility on Linux. The pinned
CTranslate2 4.9.0 Python wheel uses CUDA 12.x for NVIDIA acceleration; it
must not be assumed to work with this driver. Do not upgrade a driver on a
Stremio server solely to enable translation without separate compatibility
and rollback testing. Hardware-specific compute capability/model support
may impose additional restrictions.

Reference:
https://docs.nvidia.com/cuda/archive/12.5.0/cuda-toolkit-release-notes/index.html

## Possible future backend routes

1. **Intel-only host** — retain CPU inference as production-safe fallback;
   evaluate OpenVINO GPU in an isolated sidecar, first verifying its OpenCL
   runtime and GPU device availability, then converting an appropriate
   translation model and checking exact output/timestamps.
2. **Intel + compatible NVIDIA** — reserve Intel VAAPI for Stremio, optionally
   use a separately validated CUDA NVIDIA device for the subtitle worker.
   Observe GPU memory pressure and avoid affecting live playback.
3. **Intel + legacy NVIDIA 920M** — CPU remains the recommended default until
   runtime requirements have passed; do not claim CUDA readiness from PCI
   enumeration.

## QA result

- Standalone host inventory executed on `stremiosrv` without changes to
  Stremio containers or GPU drivers.
- Isolated Debian regression: **44 passed, 1 skipped** across the subtitle
  translator and independent GPU inventory suites.
- No model was converted to OpenVINO and no Intel GPU inference benchmark
  has been performed.
- No deployment to the production Stremio environment.

## Additional read-only capability check — stremiosrv

A separate host probe confirmed that Intel VAAPI is operational through
`LIBVA_DRIVER_NAME=i965` and `/dev/dri/renderD129`, reporting video
codec profiles. This **does not** establish Intel GPU inference capability.

Host Python has **no OpenVINO, dpctl/SYCL or CTranslate2 module installed**.
The only detected OpenCL vendor ICD is `nvidia.icd`, not an Intel compute
runtime. No Intel OpenVINO GPU model/runtime has therefore been validated.
Installation of Intel OpenCL/Level Zero drivers on this Stremio machine
would be a separate, potentially disruptive operational change, not
something the inventory tool should perform.

For this inspected hardware, safe default remains **CPU translation** until
an inference-capable GPU backend is separately proven on an isolated
development host. A device assigned to Stremio for VAAPI is not automatically
eligible for subtitle inference, and a free PCI GPU is not proof of
runtime/model compatibility.

## OpenVINO 2026 independent QA probe — 2026-11-12

The **Debian 13 QA machine** has an Intel UHD Graphics 630 (PCI
`8086:3e92`), a DRM render node `/dev/dri/renderD128`, and an existing,
working CTranslate2/SentencePiece CPU translator. OpenVINO 2026.4.1 was
installed **only inside the isolated Python venv** at
`/tmp/subtitle-translator-qa-20261112/.venv`.

Direct device discovery via `openvino.Core().available_devices` returned:

```text
['CPU']
CPU Intel(R) Core(TM) i5-9500T CPU @ 2.20GHz
```

The host has no registered `/etc/OpenCL/vendors` Intel ICD. The Intel
GPU's DRM render node is **not** enough to provide the OpenVINO GPU plugin's
required compute runtime. No GPU inference or speed-up can be claimed.

The upstream OpenVINO Intel GPU plugin uses OpenCL; it can target some older
Gen9 hardware in principle, but Linux runtime/driver compatibility is a
separate hard requirement, especially for legacy integrated graphics.
Do **not** replace or modify VAAPI/i915 drivers on the actual Stremio
machine merely to make an Intel GPU appear in OpenVINO.

Useful references:
- https://docs.openvino.ai/2026/about-openvino/release-notes-openvino/system-requirements.html
- https://github.com/openvinotoolkit/openvino/blob/master/src/plugins/intel_gpu/README.md
- https://dgpu-docs.intel.com/overview/supported-hardware/legacy-gpus.html
- https://opennmt.net/CTranslate2/hardware_support.html

### New standalone runtime diagnostic

```bash
# Run on a separate QA host, where OpenVINO may be installed.
python3 scripts/subtitle-inference-probe.py

# Optionally combine with the read-only Stremio host hardware inventory:
python3 scripts/subtitle-gpu-inventory.py \
  --output /tmp/stremio-gpus.json
python3 scripts/subtitle-inference-probe.py \
  --inventory /tmp/stremio-gpus.json
```

The script reports actual OpenVINO devices and actual CTranslate2 CUDA
capability. If a GPU is enumerated, it also tries a tiny OpenVINO arithmetic
compute graph, but deliberately sets
`gpuTranslationModelValidated=false` and
`readyForSubtitleTranslation=false`: the **Argos/CTranslate2 neural
translation model cannot be used with OpenVINO merely by changing the device
name**. It needs a suitable original-model conversion to OpenVINO IR and
a full neural machine translation adapter (tokenization, decoding, beam search,
quality comparison, concurrency and timeout handling).

Similarly, a NVIDIA GPU merely visible in host PCI data is not assumed
exclusive to Subtitle Translator. An explicit `NVIDIA_VISIBLE_DEVICES=all`
or a Docker GPU DeviceRequest on the Stremio container marks the GPU
`potentiallySharedWithStremio=true` even without a direct NVIDIA DRM mount.
Do not reserve such a GPU for translation without a capacity and usage
assessment.

**Verified QA:** 49 targeted tests passed, 1 skipped (prior to the latest
minor standalone probe cleanup); no end-to-end OpenVINO GPU model has been
converted or executed. CPU remains the only verified translation backend
on the Intel-only QA machine. No changes were made to Stremio's transcoding
devices, VAAPI settings, drivers, main branch or public releases.

### Acceptance criteria to enable Intel GPU inference

1. The independent probe must report an Intel OpenVINO `GPU` device and
   a passing GPU arithmetic smoke test.
2. A compatible neural translation model in original exportable format
   must be converted to and compiled as OpenVINO IR for that GPU; a
   `.argosmodel` containing only CTranslate2 `model.bin` is insufficient.
3. Compare output text and preserved SRT timing with the existing
   CTranslate2 CPU reference on the same sample set.
4. Measure first-translation and warm-cache latency while Stremio
   transcodes on Intel VAAPI; refuse GPU acceleration if it degrades playback.
5. Make GPU use opt-in until model quality, metrics and rollback pass.
