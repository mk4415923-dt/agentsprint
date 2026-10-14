# Deployment layout note

This is the **Railway FastAPI backend** extracted from AEGIS V4. To deploy the full application with a **Vercel frontend**, use the **top-level `../README.md`** inside the `AEGIS_V4_Vercel_Deployment` folder. The pre-existing `README.md` in this backend folder covers the older localhost application.

The backend's persistent files are relocated to `AEGIS_DATA_DIR` (set it to `/data` on Railway, with a mounted volume). `AEGIS_PUBLIC_URL` allows the Vercel frontend origin to make writes and secures login cookies for HTTPS; `AEGIS_GOOGLE_REDIRECT_URI` controls the Google OAuth callback. Never put real credentials into the repository.
