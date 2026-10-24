"""Compile native icon compositor without changing system Swift module maps."""
import pathlib,json,subprocess,os,tempfile
root=pathlib.Path(__file__).resolve().parents[2];stage=pathlib.Path(tempfile.mkdtemp(prefix='verity-icon-build-'))
legacy=pathlib.Path('/Library/Developer/CommandLineTools/usr/include/swift/module.modulemap');current=legacy.with_name('bridging.modulemap');extra=[]
if legacy.exists() and current.exists() and legacy.read_text().split('module SwiftBridging',1)[1]==current.read_text().split('module SwiftBridging',1)[1]:
 empty=stage/'empty.modulemap';empty.write_text('// Duplicate declaration hidden only for this invocation\n');overlay=stage/'overlay.json';overlay.write_text(json.dumps({'version':0,'case-sensitive':'false','roots':[{'name':str(legacy),'type':'file','external-contents':str(empty)}]}));extra=['-vfsoverlay',str(overlay),'-Xcc','-ivfsoverlay','-Xcc',str(overlay)]
sdk=subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip();env={k:v for k,v in os.environ.items() if k not in ('CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','OBJC_INCLUDE_PATH')}
subprocess.run(['xcrun','swiftc','-swift-version','5','-sdk',sdk,'-module-cache-path',str(stage/'cache'),*extra,'-target','arm64-apple-macosx12.0',str(root/'tools/desktop/build_app_icon.swift'),'-o',str(stage/'build-icon'),'-framework','AppKit'],check=True,env=env)
subprocess.run([str(stage/'build-icon'),str(root/'apps/desktop/assets')],check=True)
