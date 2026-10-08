"""Test your Gemini key, proxy and certificates:  python check_gemini.py"""
from agents.llm import check_connection
from common.config import load_settings, output_dir

if __name__ == "__main__":
    settings = load_settings()
    print(check_connection(settings, output_dir(settings)))
