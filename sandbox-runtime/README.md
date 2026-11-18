# CPU classroom runtime

The runtime installs its classroom dependencies at image build time. It does
not grant sandbox code network access or permission to change system packages.

| Use | Packages |
| --- | --- |
| Arrays and scientific computing | NumPy, SciPy, SymPy |
| Tables, CSV, Excel | Pandas, openpyxl |
| Machine learning and text features | scikit-learn |
| Plotting and images | Matplotlib, Seaborn, Pillow, Noto CJK fonts |
| Chinese text processing | jieba, regex |
| CPU neural networks and NLP | PyTorch, Transformers, tokenizers, SentencePiece, safetensors |
| Graphs and utilities | NetworkX, tqdm, PyYAML |
| Interactive execution | ipykernel |

`requirements.in` declares the supported package families. `requirements.lock`
pins the Linux/Python 3.11 resolution, including transitive dependencies and
download hashes. PyTorch remains pinned separately to its CPU-only wheel index.
Build runs `pip check` and a PyTorch/NumPy round trip. Refresh the lock with:

```powershell
uv pip compile sandbox-runtime/requirements.in --python-version 3.11 --python-platform x86_64-manylinux_2_28 --generate-hashes --output-file sandbox-runtime/requirements.lock
```

Matplotlib uses the headless Agg backend and Chinese-capable fonts. Save plots
under `/workspace/artifacts` for artifact delivery, or under `/files` when the
writable platform-files bridge is enabled. Caches go to writable `/tmp` paths.
BLAS/OpenMP default to one thread to fit the shared 2-core host.

Transformers supports local model configuration and local model/tokenizer files.
No pretrained weights or large corpora are bundled; `from_pretrained` must use
an available local path. Hub downloads are disabled. jieba includes its own
dictionary and can tokenize without a download.

CI runs `/opt/nova-runtime/verify_profile.py` in a hardened runsc container.
The acceptance covers CPU tensor/NumPy interoperability, CSV/Excel, a linear
system, TF-IDF, Chinese segmentation, Unicode regex, safe YAML, graph paths,
symbolic math, safetensors, a tiny randomly initialized BERT, and Chinese PNG
rendering. It performs no network or model downloads. Normal runtime health
probes check only PyTorch and NumPy interoperability; they do not import the
whole classroom suite into every idle kernel.

To inspect installed versions from sandbox code:

```python
from importlib.metadata import version
for name in ("numpy", "pandas", "scipy", "scikit-learn", "torch", "transformers"):
    print(name, version(name))
```

Existing containers keep their original installed packages until the Manager
replaces them. Deploy the rebuilt runtime digest and reset the runtime to use
the updated profile; merely refreshing the browser does not upgrade a container.
