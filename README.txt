================================================================================
                           PC REMOTE PRO - VERSION 4.0
       Universal Telegram PC Management & Smart Security Hub (Enterprise Edition)
                  Developed with Passion by Mohammed Shaheed
================================================================================

[+] ABOUT THE APPLICATION:
--------------------------------------------------------------------------------
PC Remote Pro v4.0 is an autonomous, enterprise-grade Windows administration
and surveillance application controlled entirely via Telegram. It offers complete
remote control, smart self-healing network persistence, visual surveillance,
and command execution from anywhere in the world through your smartphone.

[+] WHAT'S NEW IN VERSION 4.0:
--------------------------------------------------------------------------------
1. COLLAPSIBLE MOBILE MENU (حل مشكلة شاشة الهاتف وطي القائمة):
   - Added collapsible keyboard mode (is_persistent=False) to prevent the mobile
     keyboard from blocking the screen.
   - Dedicated "[ 🔽 طي وإخفاء القائمة ]" button and /hide command to hide the
     keyboard completely into a single unobtrusive inline button.

2. SMART NETWORK AUTO-HEAL WATCHDOG (نظام فحص وصيانة الشبكة الذاتي):
   - Continuous 60-second background watchdog checking internet reachability.
   - If internet connection drops while the user is away:
     * Scans saved Wi-Fi profiles and reconnects to known networks automatically.
     * Flushes Windows DNS resolver cache (ipconfig /flushdns).
     * Renews DHCP lease and adapter stack (ipconfig /renew).
     * Re-enables Wi-Fi network interface if disabled.
     * Sends an instant Telegram notification as soon as connection is restored.
   - Optional 1-minute heartbeat notification toggle.

3. PC APPLICATIONS EXPLORER & LAUNCHER (فحص وتشغيل برامج الكمبيوتر):
   - Discovers all installed applications via Start Menu shortcuts and Registry.
   - Interactive paginated Telegram catalog (8 apps per page with Next/Prev).
   - Fast keyword search (e.g., chrome, word, excel, revit, calc, telegram).
   - Launch by direct file/document path.
   - Automatically captures and sends a verification screenshot after opening.

4. CMD & PYTHON TERMINAL SUBSYSTEM (موجه الأوامر ومحرك تشغيل بايثون):
   - Execute CMD / PowerShell commands remotely and receive clean terminal output.
   - Open a real visible Command Prompt window on the desktop with photo proof.
   - Execute local .py scripts or documents uploaded directly to the chat.
   - Write and execute Python code snippets directly inside Telegram chat.

5. AUTOMATIC STARTUP SNAPSHOTS (صور الإقلاع التلقائية):
   - On PC boot/first launch, automatically captures and sends:
     * Ultra-HD Multi-monitor screenshot.
     * Real-time webcam snapshot.
     * Exact timestamp, hostname, and resolution.

[+] KEYBOARD BOT COMMANDS:
--------------------------------------------------------------------------------
  /start, /menu   : Open Main Dashboard
  /hide, /fold    : Fold/Hide keyboard menu for mobile comfort
  /apps           : Open PC Applications Explorer & Launcher
  /cmd, /py       : Terminal & Python Execution Engine
  /heal, /netheal : Smart Network Diagnostics & Auto-Repair
  /screen         : Capture Ultra-HD screenshot (PNG + Document)
  /webcam         : Capture HD webcam photo
  /mic            : Record ambient audio from microphone (up to 120s)
  /specs          : Display complete PC Hardware Specifications
  /wifi           : Network IP, Wi-Fi SSID, and connection report
  /wifipass       : View and search saved Wi-Fi passwords
  /guard          : Security Guard settings and intrusion log
  /uptime         : View system uptime since last reboot
  /wake           : Wake up PC display and simulate activity
  /battery        : Battery percentage and charging status
  /kill           : Terminate unresponsive or heavy processes
  /update         : Check and download updates from GitHub Releases

[+] INSTALLATION & USAGE:
--------------------------------------------------------------------------------
Method 1 (Installer):
  Run Output\PC_Remote_Pro_Setup_v4.0.exe and follow the wizard.
  Check "Run on Windows Startup" for 24/7 background availability.

Method 2 (Source Code):
  pip install customtkinter pillow psutil python-telegram-bot pystray opencv-python
  python bot_control.py

Configuration:
  1. Open the application on your computer.
  2. Paste your Telegram Bot Token from @BotFather.
  3. Paste your Telegram Chat ID from @userinfobot.
  4. Click "Save & Start".

================================================================================
                    Developed by: Mohammed Shaheed
                    Release Version: v4.0.0 (October 2026)
================================================================================

