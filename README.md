# Scalp Bidang

Aplikasi baru untuk scraping bidang BHUMI tanpa Laravel dan tanpa UI web.

Fokus engine ini:

- async request dengan `httpx` agar throughput lebih tinggi
- grid sampling berbasis polygon kecamatan/kelurahan
- adaptive query profile supaya satu hit bisa menarik lebih banyak bidang
- dedup geometri di memori dan opsional skip data yang sudah ada di PostgreSQL
- output ke PostgreSQL, GeoJSON, CSV, dan JSON summary
- mode live bridge ke exporter BHUMI polygon untuk kelurahan/kecamatan saat endpoint publik gagal

## Cara pakai

Konfigurasi database dibaca dari file `.env` di root proyek. Isi default sudah disediakan, dan bisa disalin ulang dari `.env.example` jika perlu:

```powershell
copy .env.example .env
```

Variabel utama:

- `SCALP_DB_HOST`
- `SCALP_DB_PORT`
- `SCALP_DB_USER`
- `SCALP_DB_PASSWORD`
- `SCALP_DB_NAME`
- `SCALP_DB_TABLE`

Alias `DB_HOST`, `DB_PORT`, `DB_USERNAME`, `DB_PASSWORD`, dan `DB_DATABASE` juga didukung jika ingin memakai gaya env umum.

```powershell
cd c:\laragon\www\bandungkab\scalp-bidang
python run.py list-areas --level kecamatan
python run.py scrape --polygon-db-source kecamatan --area-ids 320437 --coverage overpower
python run.py scrape --polygon-db-source kecamatan --limit-per-area 0 --coverage overpower
python run.py scrape --polygon-db-source kecamatan --areas Soreang --no-postgres --export-files
python run.py serve --host 127.0.0.1 --port 5055
```

## Preset coverage

- `balanced`: cepat untuk batch biasa
- `aggressive`: mode cepat untuk ambil data banyak
- `saturation`: paling rapat dan paling berat
- `bhumi-full`: sweep sangat rapat untuk polygon besar/rumit
- `overpower`: mode paling brutal, tambah perimeter sweep dan adaptive reseed dari bidang yang sudah ketemu

## Catatan

- Untuk `polygon_db_source=kelurahan` atau `kecamatan`, engine akan memakai jalur live bridge ke helper BHUMI agar polygon GeoJSON bisa di-resolve dari `getBidang`.
- Untuk source lain, engine tetap bisa memakai endpoint publik BHUMI `GetFeatureInfo`.
- Engine baru memaksimalkan jumlah data per sweep dengan concurrency async, ukuran query adaptif, perimeter sweep, dan adaptive reseed multi-pass.
- Untuk monitoring yang lebih mudah, jalankan `python run.py serve` lalu buka dashboard Leaflet di `http://127.0.0.1:5055/`.
- File `output/latest_run.json` akan diperbarui otomatis setiap selesai proses scrape.
- Path helper live bisa diubah lewat `SCALP_BHUMI_HELPER_PHP`.
