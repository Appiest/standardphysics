export const API_ORIGIN = process.env.SP_API_ORIGIN ?? (process.env.SP_API_PORT ? `http://127.0.0.1:${process.env.SP_API_PORT}` : "http://127.0.0.1:8788");
