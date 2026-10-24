"""Build a relocatable Apple Silicon pilot app with a frozen Python backend.

Use a dedicated build environment. This produces an ad-hoc signed local pilot,
not a Developer ID signed/notarized release. No model or provider credentials
are included. Native dependencies such as ffmpeg remain optional and report
unavailability through existing media diagnostics.
"""
import argparse
import hashlib
import json
import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile


def package(repo, output, activation_public_key=None):
    repo, output = pathlib.Path(repo).resolve(), pathlib.Path(output).absolute()
    if output.exists() or output.is_symlink() or output.suffix != ".app":
        raise ValueError("Choose a new .app output path")
    if sys.platform != "darwin":
        raise ValueError("Native packaging requires macOS")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix=".verity-package-", dir=output.parent))
    try:
        app = stage / "Verity.app"
        contents = app / "Contents"
        macos, resources = contents / "MacOS", contents / "Resources"
        macos.mkdir(parents=True); resources.mkdir()
        src = repo / "apps/desktop"
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
               "--name", "verity-worker", "--distpath", str(stage/"dist"),
               "--workpath", str(stage/"work"), "--specpath", str(stage),
               "--paths", str(repo), "--paths", str(repo/"apps/node"),
               "--collect-submodules", "uvicorn", "--collect-submodules", "google.auth",
               "--add-data", str(repo/"apps/node/swarmtracker_node_ui/templates")+":swarmtracker_node_ui/templates",
               "--add-data", str(repo/"apps/node/swarmtracker_node_ui/static")+":swarmtracker_node_ui/static",
               str(src/"worker.py")]
        subprocess.run(cmd, cwd=repo, check=True)
        shutil.copytree(stage/"dist/verity-worker", resources/"backend")
        sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
        extra=[]
        legacy=pathlib.Path("/Library/Developer/CommandLineTools/usr/include/swift/module.modulemap")
        current=legacy.with_name("bridging.modulemap")
        if legacy.is_file() and current.is_file() and "module SwiftBridging" in legacy.read_text() and legacy.read_text().split("module SwiftBridging",1)[1]==current.read_text().split("module SwiftBridging",1)[1]:
            empty=stage/"empty.modulemap";empty.write_text("// Redundant legacy declaration hidden for this compilation only.\n")
            overlay=stage/"swift-overlay.json"
            overlay.write_text(json.dumps({"version":0,"case-sensitive":"false","roots":[{"name":str(legacy),"type":"file","external-contents":str(empty)}]}))
            extra=["-vfsoverlay",str(overlay),"-Xcc","-ivfsoverlay","-Xcc",str(overlay)]
            print("Using an isolated overlay for duplicate SwiftBridging declarations",flush=True)
        env={k:v for k,v in __import__('os').environ.items() if k not in ("CPATH","C_INCLUDE_PATH","CPLUS_INCLUDE_PATH","OBJC_INCLUDE_PATH")}
        subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-sdk", sdk,
                        "-module-cache-path", str(stage/"swift-cache"), *extra,
                        "-target", "arm64-apple-macosx12.0", str(src/"Verity.swift"),
                        "-o", str(macos/"Verity"), "-framework", "AppKit", "-framework", "WebKit"], check=True,env=env)
        if activation_public_key:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            key=pathlib.Path(activation_public_key).read_bytes()
            if not isinstance(serialization.load_pem_public_key(key),Ed25519PublicKey):raise ValueError("Ed25519 activation public key required")
            (resources/"activation-public.pem").write_bytes(key)
        config = dict(python="backend/verity-worker", worker="backend/verity-worker", repo=".", bundled=True)
        (resources/"desktop-config.json").write_text(json.dumps(config, indent=2))
        from verity_core.desktop_version import DESKTOP_VERSION
        info = dict(CFBundleIdentifier="ai.swarmtracker.verity.pilot", CFBundleName="Verity by SwarmTracker",
                    CFBundleDisplayName="Verity by SwarmTracker", CFBundleExecutable="Verity", CFBundlePackageType="APPL",
                    CFBundleShortVersionString=DESKTOP_VERSION, LSMinimumSystemVersion="15.0", NSHighResolutionCapable=True)
        icon = src/"assets/AppIcon.icns"
        if icon.is_file():
            shutil.copy2(icon, resources/"AppIcon.icns"); info["CFBundleIconFile"]="AppIcon"
        with (contents/"Info.plist").open("wb") as f: plistlib.dump(info, f)
        names=subprocess.check_output(["git","-C",str(repo),"ls-files","-z","--cached","--others","--exclude-standard"]).decode().split("\0")
        digest=hashlib.sha256()
        for name in sorted(n for n in names if n):
            f=repo/name
            if f.is_file():digest.update(name.encode()+b"\0"+f.read_bytes()+b"\0")
        source_dirty=bool(subprocess.check_output(["git","-C",str(repo),"status","--porcelain"]))
        (resources/"pilot-build.json").write_text(json.dumps(dict(source_files_sha256=digest.hexdigest(),source_tree_dirty=source_dirty,
            bundle_runtime=True, signing="ad-hoc only", notarized=False,
            python=sys.version.split()[0], source_commit=subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()), indent=2))
        # PyInstaller signs its embedded native binaries. Sign the outer shell
        # and app ad-hoc for local execution; distribution signing is separate.
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        app.rename(output)
    finally:
        shutil.rmtree(stage)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--activation-public-key")
    args=parser.parse_args()
    print(package(args.repo, args.output,args.activation_public_key))
