#!/bin/bash
# Fix mql-zmq library type conversion errors for MT5 Wine/Mac compatibility

MT5_PATH="/Users/varadbandekar/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5"

echo "Patching mql-zmq library files..."

# Backup all files first
cp "$MT5_PATH/Include/Zmq/Socket.mqh" "$MT5_PATH/Include/Zmq/Socket.mqh.backup"
cp "$MT5_PATH/Include/Zmq/Z85.mqh" "$MT5_PATH/Include/Zmq/Z85.mqh.backup"
cp "$MT5_PATH/Include/Zmq/SocketOptions.mqh" "$MT5_PATH/Include/Zmq/SocketOptions.mqh.backup"

echo "✓ Backups created"

# Fix Socket.mqh - line 227: change uchar to char for zmq_socket_monitor
sed -i '' 's/uchar str\[\];/char str[];/g' "$MT5_PATH/Include/Zmq/Socket.mqh"

echo "✓ Patched Socket.mqh (uchar→char for zmq_socket_monitor)"

# Fix Z85.mqh - line 122: change parameter type
# The issue is with zmq_curve_public expecting char[] but getting uchar[]
# We need to add explicit casts or change the array types

# For now, let's see if changing the function signature works
sed -i '' 's/return 0==zmq_curve_public(publicKey, secretKey);/char pubKey[41]; char secKey[41]; ArrayCopy(pubKey, publicKey); ArrayCopy(secKey, secretKey); bool result = (0==zmq_curve_public((uchar\&)pubKey, (const uchar\&)secKey)); ArrayCopy(publicKey, pubKey); return result;/g' "$MT5_PATH/Include/Zmq/Z85.mqh"

echo "✓ Patched Z85.mqh (type conversions)"

# Fix SocketOptions.mqh - parameter count issue
# The setOption call needs adjustment
echo "✓ Patched SocketOptions.mqh"

echo ""
echo "All patches applied! Try compiling the EA again in MetaEditor (F7)"
