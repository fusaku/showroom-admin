"""Draw the application icon using native vector paths and text."""
from pathlib import Path
import subprocess
from AppKit import NSImage,NSColor,NSBezierPath,NSMakeRect,NSFont,NSFontAttributeName,NSForegroundColorAttributeName,NSBitmapImageRep,NSPNGFileType
from Foundation import NSAttributedString
root=Path(__file__).resolve().parents[1];folder=root/'build/Showroom.iconset';folder.mkdir(parents=True,exist_ok=True)
for size in (16,32,64,128,256,512,1024):
    image=NSImage.alloc().initWithSize_((size,size));image.lockFocus()
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.23,0.34,0.9,1).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(size*.06,size*.06,size*.88,size*.88),size*.2,size*.2).fill()
    attrs={NSFontAttributeName:NSFont.boldSystemFontOfSize_(size*.67),NSForegroundColorAttributeName:NSColor.whiteColor()}
    text=NSAttributedString.alloc().initWithString_attributes_('S',attrs);extent=text.size()
    text.drawAtPoint_(((size-extent.width)/2,(size-extent.height)/2+size*.025))
    image.unlockFocus()
    rep=NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    data=rep.representationUsingType_properties_(NSPNGFileType,{})
    if size<=512:data.writeToFile_atomically_(str(folder/f'icon_{size}x{size}.png'),True)
    if size>=32 and size//2 in (16,32,128,256,512):data.writeToFile_atomically_(str(folder/f'icon_{size//2}x{size//2}@2x.png'),True)
subprocess.run(['/usr/bin/iconutil','-c','icns',str(folder),'-o',str(root/'build/Showroom.icns')],check=True)
