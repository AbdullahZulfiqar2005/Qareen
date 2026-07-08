#!/bin/bash
# Package the extension directory files into a single .xpi package for Firefox.
# .xpi is just a zip archive containing manifest.json at its root level.

echo "Packaging extension..."
if [ -f qareen.xpi ]; then
    rm qareen.xpi
fi

cd extension
zip -r ../qareen.xpi manifest.json content.js background.js
cd ..

echo "--------------------------------------------------------"
echo "Extension packaged successfully as: qareen.xpi"
echo "--------------------------------------------------------"
echo "Installation Instructions for Firefox:"
echo "1. If using Firefox Developer Edition:"
echo "   - Go to 'about:config' and set 'xpinstall.signatures.required' to false."
echo "   - Go to 'about:addons', click the gear icon, and select 'Install Add-on From File'."
echo "   - Choose 'qareen.xpi' and approve the installation. It will stay permanently."
echo "2. If using standard release Firefox:"
echo "   - Upload 'qareen.xpi' as an 'unlisted' addon on https://addons.mozilla.org/developers/."
echo "   - Mozilla will sign it and provide a signed version. Download it and install it permanently."
echo "--------------------------------------------------------"
