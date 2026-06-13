#!/data/data/com.termux/files/usr/bin/bash
set -e

echo "▐█▌ TradingAgents — Termux Setup ▐█▌"
echo ""

# Install system deps
echo "→ Updating packages..."
pkg update -y && pkg upgrade -y
pkg install python git clang -y

# Create venv
echo "→ Setting up Python virtual environment..."
python -m venv .venv
source .venv/bin/activate

# Install pip deps
echo "→ Installing Python dependencies..."
pip install --upgrade pip wheel setuptools
pip install -r requirements-web.txt
pip install -e .

# Create run script
cat > web/start.sh << 'SCRIPT'
#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")"
source ../.venv/bin/activate
export DEEPSEEK_API_KEY="not-needed-for-free-models"
export OPENAI_API_KEY="not-needed-for-free-models"
echo ""
echo "▐█▌ TradingAgents Web Server ▐█▌"
echo "   Local:  http://localhost:8000"
echo "   Network: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000"
echo ""
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
SCRIPT
chmod +x web/start.sh

echo ""
echo "▐█▌ Setup complete! ▐█▌"
echo ""
echo "  Run the web server:"
echo "    cd web && ./start.sh"
echo ""
echo "  Open in browser: http://localhost:8000"
