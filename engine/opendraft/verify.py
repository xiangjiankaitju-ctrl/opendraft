"""Installation verification for OpenDraft."""

import sys
import os
import subprocess
from pathlib import Path


def check_python_version():
    """Check if Python version is compatible."""
    version = sys.version_info
    required = (3, 10)

    print(f"🐍 Python version: {version.major}.{version.minor}.{version.micro}")

    if version >= required:
        print(f"   ✅ Compatible (>= {required[0]}.{required[1]})")
        return True
    else:
        print(f"   ❌ Too old (need >= {required[0]}.{required[1]})")
        return False


def check_dependencies():
    """Check if all required dependencies are installed."""
    required_packages = [
        "anthropic",
        "openai",
        "google.genai",
        "pybtex",
        "citeproc",
        "yaml",
        "markdown",
        "weasyprint",
        "docx",
        "requests",
        "bs4",
        "lxml",
        "dotenv",
        "rich",
    ]

    print("\n📦 Dependencies:")
    all_installed = True

    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
            print(f"   ✅ {package}")
        except ImportError:
            print(f"   ❌ {package} (missing)")
            all_installed = False

    return all_installed


def check_api_keys():
    """Check if API keys are configured."""
    print("\n🔑 API Keys (from environment):")

    keys = {
        "LLM_BASE_URL": "Generic endpoint base URL",
        "LLM_API_KEY": "Generic endpoint API key",
        "LLM_MODEL": "Generic endpoint model",
    }

    env_file = Path(".env")
    if env_file.exists():
        print(f"   ℹ️  Found .env file: {env_file.absolute()}")
        from dotenv import load_dotenv
        load_dotenv()
    else:
        print(f"   ⚠️  No .env file found (using system environment)")

    found_any = False
    has_generic = bool(os.getenv("LLM_BASE_URL") and os.getenv("LLM_API_KEY"))
    for key, name in keys.items():
        value = os.getenv(key)
        if value:
            # Mask the key for security
            masked = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
            print(f"   ✅ {name}: {masked}")
            found_any = True
        else:
            print(f"   ⚠️  {name}: Not set")

    if not found_any:
        print("\n   ⚠️  WARNING: No API keys configured!")
        print("   You need either a provider API key OR LLM_BASE_URL+LLM_API_KEY.")
        print("   See: https://github.com/federicodeponte/opendraft#setup")

    return found_any or has_generic


def check_pdf_engines():
    """Check if PDF generation engines are available."""
    print("\n📄 PDF Engines:")

    engines = [
        ("weasyprint", "WeasyPrint (recommended)"),
        ("pandoc", "Pandoc"),
        ("libreoffice", "LibreOffice"),
    ]

    available_count = 0

    for cmd, name in engines:
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                version = result.stdout.split("\n")[0][:50]
                print(f"   ✅ {name}: {version}")
                available_count += 1
            else:
                print(f"   ❌ {name}: Not available")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print(f"   ❌ {name}: Not available")

    if available_count == 0:
        print("\n   ⚠️  WARNING: No PDF engines found!")
        print("   You need at least one PDF engine to export theses.")
        print("   Install WeasyPrint: pip install weasyprint")

    return available_count > 0


def check_docx_export_stack():
    """Check production DOCX export dependencies."""
    print("\n📝 DOCX Export Stack:")
    checks = []

    commands = [
        ("pandoc", "Pandoc"),
        ("fc-match", "fontconfig"),
    ]

    libreoffice_cmd = "soffice"
    try:
        subprocess.run([libreoffice_cmd, "--version"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        libreoffice_cmd = "libreoffice"

    commands.append((libreoffice_cmd, "LibreOffice"))

    for cmd, name in commands:
        try:
            result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                version = (result.stdout or result.stderr).split("\n")[0][:70]
                print(f"   ✅ {name}: {version}")
                checks.append(True)
            else:
                print(f"   ❌ {name}: Not available")
                checks.append(False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print(f"   ❌ {name}: Not available")
            checks.append(False)

    for font_name, label in [
        ("Noto Serif CJK SC", "Noto CJK fonts"),
        ("Liberation Serif", "Liberation fonts"),
    ]:
        try:
            result = subprocess.run(["fc-match", font_name], capture_output=True, text=True, timeout=5)
            found = result.returncode == 0 and result.stdout.strip()
            print(f"   {'✅' if found else '❌'} {label}: {result.stdout.splitlines()[0] if found else 'Not available'}")
            checks.append(found)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print(f"   ❌ {label}: fontconfig not available")
            checks.append(False)

    template_dir = Path(__file__).resolve().parent.parent / "templates"
    for template in ["zh_reference.docx", "en_reference.docx"]:
        exists = (template_dir / template).exists()
        print(f"   {'✅' if exists else '❌'} {template}")
        checks.append(exists)

    if not all(checks):
        print("\n   ⚠️  Production DOCX export requires pandoc, LibreOffice/headless, fontconfig, Noto CJK fonts, Liberation fonts, and python-docx.")

    return all(checks)


def check_file_structure():
    """Check if project structure is intact."""
    print("\n📁 Project Structure:")

    required_dirs = [
        "utils",
        "concurrency",
        "prompts",
        "examples",
    ]

    all_exist = True

    for dir_name in required_dirs:
        dir_path = Path(dir_name)
        if dir_path.exists() and dir_path.is_dir():
            file_count = len(list(dir_path.glob("**/*.py")))
            print(f"   ✅ {dir_name}/ ({file_count} Python files)")
        else:
            print(f"   ❌ {dir_name}/ (missing)")
            all_exist = False

    return all_exist


def verify_installation():
    """Main verification function."""
    print("=" * 60)
    print("  OpenDraft - Installation Verification")
    print("=" * 60)

    results = {}

    results["python"] = check_python_version()
    results["dependencies"] = check_dependencies()
    results["api_keys"] = check_api_keys()
    results["pdf_engines"] = check_pdf_engines()
    results["docx_export_stack"] = check_docx_export_stack()
    results["structure"] = check_file_structure()

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    all_passed = all(results.values())

    for check, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status:10} {check.replace('_', ' ').title()}")

    print("=" * 60)

    if all_passed:
        print("\n✅ Installation verified successfully!")
        print("   You're ready to generate academic theses.")
        print("\nNext steps:")
        print("  1. Review QUICKSTART.md for usage examples")
        print("  2. Run: python tests/scripts/test_ai_pricing_draft.py")
        print("  3. Check examples/ for sample theses")
        return 0
    else:
        print("\n⚠️  Installation has issues (see above)")
        print("\nTroubleshooting:")
        print("  - Install missing dependencies: pip install -e .")
        print("  - Configure API keys in .env file")
        return 1


if __name__ == "__main__":
    sys.exit(verify_installation())
