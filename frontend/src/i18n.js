// Interface language: English and Bahasa Indonesia for navigation and page titles (FR-CORE-035).
const ID = {
  "Overview": "Ringkasan", "Dashboard": "Dasbor", "Alerts": "Peringatan", "Analysis": "Analisis", "Go-arounds": "Go-around",
  "SPI register": "Daftar SPI", "Route deviations": "Deviasi rute", "Complexity": "Kompleksitas", "Early warning": "Peringatan dini",
  "Reports": "Laporan", "Administration": "Administrasi", "Reference data": "Data referensi", "Parameters": "Parameter",
  "Jobs": "Proses", "Users": "Pengguna", "Connections": "Koneksi", "Audit log": "Log audit", "Account": "Akun",
  "Sign out": "Keluar", "Sign in": "Masuk", "Username": "Nama pengguna", "Password": "Kata sandi",
  "Map": "Peta", "Trend": "Tren", "Events": "Kejadian", "Heat table": "Tabel panas", "Risk factors": "Faktor risiko",
  "Validation": "Validasi", "From": "Dari", "To": "Sampai", "Aerodrome": "Bandar udara", "Runway": "Landas pacu",
  "Operator": "Operator", "Export CSV": "Ekspor CSV", "Export Excel": "Ekspor Excel", "Save": "Simpan", "Cancel": "Batal",
};
let lang = localStorage.getItem("navruna_lang") || "en";
export function setLang(l) { lang = l; try { localStorage.setItem("navruna_lang", l); } catch { /* */ } }
export const getLang = () => lang;
export const t = (s) => (lang === "id" && ID[s]) || s;
