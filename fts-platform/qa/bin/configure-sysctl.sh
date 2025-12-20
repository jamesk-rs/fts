#!/bin/bash
# Configure Linux kernel parameters for USRP performance
# Run with sudo: sudo ./bin/configure-sysctl.sh

set -e

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo: sudo $0"
    exit 1
fi

echo "Configuring kernel parameters for UHD/USRP..."

# Network buffer sizes (fixes UDP send/recv buffer warnings)
sysctl -w net.core.wmem_max=2500000
sysctl -w net.core.rmem_max=50000000

# Make persistent
cat > /etc/sysctl.d/99-usrp.conf << 'EOF'
# UHD/USRP network buffer sizes
net.core.wmem_max=2500000
net.core.rmem_max=50000000
EOF

echo "Created /etc/sysctl.d/99-usrp.conf"

# Real-time thread priority (fixes pthread_setschedparam warning)
# Add current user to realtime group if it exists, otherwise configure limits directly
if getent group realtime > /dev/null 2>&1; then
    SUDO_USER_NAME="${SUDO_USER:-$USER}"
    usermod -aG realtime "$SUDO_USER_NAME"
    echo "Added $SUDO_USER_NAME to realtime group"
fi

# Configure real-time limits for the user
SUDO_USER_NAME="${SUDO_USER:-$USER}"
cat > /etc/security/limits.d/99-usrp.conf << EOF
# UHD/USRP real-time thread priority
$SUDO_USER_NAME - rtprio 99
$SUDO_USER_NAME - memlock unlimited
@realtime - rtprio 99
@realtime - memlock unlimited
EOF

echo "Created /etc/security/limits.d/99-usrp.conf"

echo ""
echo "Configuration complete. Please:"
echo "  1. Log out and back in (for limits to take effect)"
echo "  2. Or reboot for all changes to take effect"
echo ""
echo "To verify settings:"
echo "  sysctl net.core.wmem_max net.core.rmem_max"
echo "  ulimit -r  # should show 99"
