from flask import Flask, jsonify, request
from flask_cors import CORS
import subprocess
import time
import os
import zipfile
import tempfile
import uuid
import json

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- FAIRY-STOCKFISH MOTOR KURULUMU ---
if os.name == 'nt':
    exe_yolu = os.path.join(BASE_DIR, "fairy-stockfish-largeboard_x86-64-bmi2.exe")
else:
    exe_yolu = "/tmp/fairy-stockfish-largeboard_x86-64"
    if not os.path.exists(exe_yolu):
        zip_path = os.path.join(BASE_DIR, "motor.zip")
        if os.path.exists(zip_path):
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall("/tmp/")
            os.chmod(exe_yolu, 0o755)

VARIANTS_DIR = tempfile.gettempdir()
DEFAULT_VARIANTS_PATH = os.path.join(BASE_DIR, "variants.ini")

@app.route('/')
def index():
    return jsonify({
        "proje": "TÜBİTAK 1001 - WSN Otonom Karar API",
        "durum": "Sistem Aktif",
        "motor": "Fairy-Stockfish Largeboard",
        "platform": "Vercel / Render",
        "api_endpoints": {
            "karar": "/api/karar",
            "health": "/api/health"
        }
    })

@app.route('/api/health', methods=['GET'])
def health():
    motor_mevcut = os.path.exists(exe_yolu)
    return jsonify({
        "durum": "aktif",
        "motor_hazir": motor_mevcut,
        "motor": "fairy-stockfish-largeboard"
    })

@app.route('/api/karar', methods=['GET', 'POST'])
def karar_al():
    baslangic = time.time()

    if request.method == 'POST':
        if request.is_json:
            veri = request.get_json() or {}
        else:
            veri = request.form.to_dict()
    else:
        veri = request.args.to_dict()

    hamle_gecmisi = veri.get('hamle_gecmisi', '')
    fen = veri.get('fen', '')
    varyant_adi = veri.get('varyant_adi', '')
    variants_ini_icerik = veri.get('variants_ini', '')

    try:
        kullanilan_variants_path = DEFAULT_VARIANTS_PATH

        if variants_ini_icerik:
            gecici_ini = os.path.join(VARIANTS_DIR, f"variants_{int(time.time()*1000)}.ini")

            if isinstance(variants_ini_icerik, dict):
                section = varyant_adi or "wsn_gelismis_ag"
                satirlar = [f"[{section}:chess]"]
                for k, v in variants_ini_icerik.items():
                    satirlar.append(f"{k} = {v}")
                ini_metin = "\n".join(satirlar)
            elif isinstance(variants_ini_icerik, str):
                ini_metin = variants_ini_icerik
            else:
                ini_metin = str(variants_ini_icerik)

            # CRITICAL FIX: Windows CRLF (\r\n) -> Linux LF (\n) normalization
            ini_metin = ini_metin.replace('\r\n', '\n').replace('\r', '\n')

            with open(gecici_ini, 'w', encoding='utf-8', newline='\n') as f:
                f.write(ini_metin)
            kullanilan_variants_path = gecici_ini

            if not varyant_adi and ini_metin:
                for satir in ini_metin.split('\n'):
                    satir = satir.strip()
                    if satir.startswith('[') and ':' in satir:
                        varyant_adi = satir.split('[')[1].split(':')[0].strip()
                        break

        # Varyant adını temizle (whitespace / \r karakterlerini süz)
        if varyant_adi:
            varyant_adi = varyant_adi.strip()

        motor = subprocess.Popen(
            exe_yolu,
            universal_newlines=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        motor.stdin.write("uci\n")
        motor.stdin.flush()

        while True:
            satir = motor.stdout.readline().strip()
            if satir == "uciok":
                break

        if os.path.exists(kullanilan_variants_path):
            variants_abs_path = os.path.abspath(kullanilan_variants_path)
            motor.stdin.write(f"setoption name VariantPath value {variants_abs_path}\n")

        if varyant_adi:
            motor.stdin.write(f"setoption name UCI_Variant value {varyant_adi}\n")

        motor.stdin.write("isready\n")
        motor.stdin.flush()

        while True:
            satir = motor.stdout.readline().strip()
            if satir == "readyok":
                break

        motor.stdin.write("ucinewgame\n")

        if fen:
            if hamle_gecmisi:
                motor.stdin.write(f"position fen {fen} moves {hamle_gecmisi}\n")
            else:
                motor.stdin.write(f"position fen {fen}\n")
        elif hamle_gecmisi:
            motor.stdin.write(f"position startpos moves {hamle_gecmisi}\n")
        else:
            motor.stdin.write("position startpos\n")

        motor.stdin.write("go depth 12\n")
        motor.stdin.flush()

        bestmove = None
        cp_skoru = None

        while True:
            satir = motor.stdout.readline().strip()

            if "score cp" in satir:
                parcalar = satir.split()
                try:
                    cp_indeksi = parcalar.index("cp")
                    cp_skoru = int(parcalar[cp_indeksi + 1])
                except (ValueError, IndexError):
                    pass

            if satir.startswith("bestmove"):
                bestmove = satir.split()[1]
                break

        motor.terminate()
        gecen_sure = round((time.time() - baslangic) * 1000, 2)

        if variants_ini_icerik and 'gecici_ini' in locals() and os.path.exists(gecici_ini):
            try:
                os.remove(gecici_ini)
            except OSError:
                pass

        return jsonify({
            "gelen_veri": hamle_gecmisi,
            "fen": fen if fen else "startpos",
            "varyant": varyant_adi if varyant_adi else "standart",
            "bestmove": bestmove,
            "cp_skoru": cp_skoru,
            "gecikme_ms": gecen_sure,
            "motor": "fairy-stockfish-largeboard"
        })

    except Exception as e:
        return jsonify({
            "hata": str(e),
            "mesaj": "Sistem motoru tetikleyemedi."
        }), 500

@app.route('/api/legalmoves', methods=['POST'])
def legal_moves():
    try:
        veri = request.get_json() or {}
        fen = veri.get('fen', '')
        varyant_adi = veri.get('varyant_adi', '')
        variants_ini_icerik = veri.get('variants_ini', '')

        gecici_ini = None

        motor = subprocess.Popen(
            exe_yolu,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        # UCI başlat ve uciok bekle
        motor.stdin.write("uci\n")
        motor.stdin.flush()
        while True:
            satir = motor.stdout.readline().strip()
            if satir == "uciok":
                break

        # Varyant dosyası varsa yükle
        if variants_ini_icerik and varyant_adi:
            dosya_adi = f"temp_variants_{uuid.uuid4().hex}.ini"
            gecici_ini = os.path.join(VARIANTS_DIR, dosya_adi)

            if isinstance(variants_ini_icerik, str):
                ini_metin = variants_ini_icerik
            else:
                ini_metin = json.dumps(variants_ini_icerik)

            ini_metin = ini_metin.replace('\r\n', '\n')

            with open(gecici_ini, "w", encoding="utf-8", newline='\n') as f:
                f.write(ini_metin)

            motor.stdin.write(f"setoption name VariantPath value {gecici_ini}\n")
            motor.stdin.flush()
            motor.stdin.write(f"setoption name UCI_Variant value {varyant_adi}\n")
            motor.stdin.flush()

        # isready/readyok bekle
        motor.stdin.write("isready\n")
        motor.stdin.flush()
        while True:
            satir = motor.stdout.readline().strip()
            if satir == "readyok":
                break

        # Pozisyon ayarla
        if fen:
            motor.stdin.write(f"position fen {fen}\n")
        else:
            motor.stdin.write("position startpos\n")
        motor.stdin.flush()

        # go perft 1 ile yasal hamleleri al
        motor.stdin.write("go perft 1\n")
        motor.stdin.flush()

        legal_moves = []
        while True:
            satir = motor.stdout.readline().strip()

            if not satir:
                continue

            if ": " in satir and "Nodes" not in satir:
                parts = satir.split(": ")
                if len(parts) == 2 and parts[1].strip().isdigit():
                    legal_moves.append(parts[0].strip())

            if "Nodes searched:" in satir:
                break

        motor.stdin.write("quit\n")
        motor.stdin.flush()
        motor.terminate()

        if gecici_ini and os.path.exists(gecici_ini):
            try:
                os.remove(gecici_ini)
            except OSError:
                pass

        return jsonify({"legal_moves": legal_moves})

    except Exception as e:
        return jsonify({"hata": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)