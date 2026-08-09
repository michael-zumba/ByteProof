import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    
    # Ensure the root directory is in sys.path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    try:
        from src.main import main
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("PyQt6"):
            print("ByteProof dependencies are missing.")
            print("Run ByteProof with the project virtual environment:")
            print("  ./venv/bin/python run.py")
            print("  (or: source venv/bin/activate  &&  python run.py)")
            print()
            print("If the venv is missing, create it with:")
            print("  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt")
            sys.exit(1)
        raise
    sys.exit(main())
