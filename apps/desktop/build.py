"""Build a development .app; external repo and Python env are required."""
import argparse
import json
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

IDENTIFIER = "ai.swarmtracker.verity.dev"

def build(output, repo, python, icon=None, overwrite=False, library_path=None):
    output, repo = Path(output).absolute(), Path(repo).resolve()
    python = Path(python).absolute()  # Preserve venv launcher path, do not resolve symlink.
    if output.suffix != ".app" or output.is_symlink():
        raise ValueError("output must be a non-symlink .app path")
    if not python.is_file() or not (repo/"apps/node/swarmtracker_node_ui/app.py").is_file():
        raise ValueError("Python interpreter or Node repository is missing")
    if library_path and not Path(library_path).is_dir():
        raise ValueError("Python library path does not exist")
    if icon and (Path(icon).suffix != ".icns" or not Path(icon).is_file()):
        raise ValueError("icon must be an existing .icns file")
    if output.exists():
        marker = output/"Contents/Resources/verity-development.json"
        if not overwrite or not marker.is_file():
            raise ValueError("existing output cannot be replaced without a managed marker and --overwrite")
        if json.loads(marker.read_text()).get("identifier") != IDENTIFIER:
            raise ValueError("refusing to replace an unrelated app")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".verity-build-", dir=output.parent))
    try:
        contents = staging/"Contents"
        macos, resources = contents/"MacOS", contents/"Resources"
        macos.mkdir(parents=True); resources.mkdir()
        src = Path(__file__).resolve().parent
        sdk = subprocess.check_output(["xcrun","--sdk","macosx","--show-sdk-path"],text=True).strip()
        subprocess.run(["xcrun","swiftc","-swift-version","5","-sdk",sdk,
                        "-target","arm64-apple-macosx12.0",
                        str(src/"Verity.swift"),"-o",str(macos/"Verity"),
                        "-framework","AppKit","-framework","WebKit"], check=True)
        shutil.copy2(src/"worker.py", resources/"worker.py")
        cfg = {"python": str(python), "worker": str(output/"Contents/Resources/worker.py"),
               "repo": str(repo), "library_path": library_path}
        (resources/"desktop-config.json").write_text(json.dumps(cfg,indent=2))
        (resources/"verity-development.json").write_text(json.dumps({"identifier":IDENTIFIER}))
        info = {"CFBundleIdentifier":IDENTIFIER,"CFBundleName":"Verity",
                "CFBundleDisplayName":"Verity","CFBundleExecutable":"Verity",
                "CFBundlePackageType":"APPL","CFBundleShortVersionString":"0.3.0-dev",
                "LSMinimumSystemVersion":"12.0","NSHighResolutionCapable":True}
        if icon:
            shutil.copy2(icon,resources/"AppIcon.icns"); info["CFBundleIconFile"]="AppIcon"
        with (contents/"Info.plist").open("wb") as f: plistlib.dump(info,f)
        if output.exists():
            old = Path(tempfile.mkdtemp(prefix=".verity-previous-",dir=output.parent))
            old.rmdir()
            output.rename(old)
            try: staging.rename(output)
            except BaseException:
                old.rename(output); raise
            shutil.rmtree(old)  # Only the previously verified managed app.
        else:
            staging.rename(output)
    finally:
        if staging.exists(): shutil.rmtree(staging)  # Only our unique staging dir.
    return output

def main():
    parser=argparse.ArgumentParser()
    for arg in ("output","repo","python"): parser.add_argument("--"+arg,required=True)
    parser.add_argument("--icon"); parser.add_argument("--python-library-path")
    parser.add_argument("--overwrite",action="store_true")
    args=parser.parse_args()
    result=build(args.output,args.repo,args.python,args.icon,args.overwrite,args.python_library_path)
    print("Built development app:",result)
    print("Requires the configured repository and Python environment; not a standalone installer.")

if __name__=="__main__": main()
