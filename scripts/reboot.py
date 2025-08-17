import os
import sys
import subprocess
import asyncio

cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(cwd)

from PerfectionBot.main import shutdown

asyncio.run(shutdown())

parent_dir = os.path.dirname(cwd)
result = subprocess.run([sys.executable, "-m", "PerfectionBot.main"], cwd=parent_dir)

if result.returncode != 0:
    print("Command failed")
    sys.exit(result.returncode)