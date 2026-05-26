# gpu-dashboard

Backend (`src/gpu_dashboard/`) + frontend (`frontend/`). stdlib `http.server`, React+Vite+Tailwind, same dark palette as slurm-mgr.

```bash
pip install -e ../gpuwatchlib -e .
python -m gpu_dashboard.server               # :8780
cd frontend && npm install && npm run dev    # :5175
```
