; Inno Setup Script for PC Remote Pro
; Installs the application to C:\Program Files\PC Remote Pro

#define MyAppName "PC Remote Pro"
#define MyAppVersion "4.0.0"
#define MyAppPublisher "Mohammed Shaheed"
#define MyAppExeName "PC_Remote_Pro.exe"
#define MyAppAssocName MyAppName + " File"
#define MyAppAssocExt ".myp"
#define MyAppAssocKey StringChange(MyAppAssocName, " ", "") + MyAppAssocExt

[Setup]
; Basic Application Info
AppId={{8B490B42-4D39-44F0-A39B-C57EF8C49B2A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output Configuration
OutputDir=c:\Users\alarabi\Desktop\my_bot\Output
OutputBaseFilename=PC_Remote_Pro_Setup_v4.0
SetupIconFile=c:\Users\alarabi\Desktop\my_bot\PC_Remote_Pro.ico
; Compression Settings
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
; Permissions & Architecture
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
CloseApplicationsFilter=PC_Remote_Pro.exe
VersionInfoVersion=3.9.0.0
VersionInfoCompany=Mohammed Shaheed
VersionInfoDescription=PC Remote Pro - Universal Remote Control Hub
VersionInfoCopyright=Copyright (C) 2026 Mohammed Shaheed
VersionInfoProductName=PC Remote Pro

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "تشغيل البرنامج تلقائياً في الخلفية عند بدء تشغيل الكمبيوتر (Run automatically on Windows startup)"; GroupDescription: "إعدادات التشغيل التلقائي (Startup Settings):"

[InstallDelete]
Type: files; Name: "{app}\PC_Remote_Pro.ico"
Type: files; Name: "{app}\PC_Remote_Pro.png"
Type: files; Name: "{app}\PC_Remote_Pro.jpg"

[Files]
Source: "c:\Users\alarabi\Desktop\my_bot\dist\PC_Remote_Pro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "c:\Users\alarabi\Desktop\my_bot\PC_Remote_Pro.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "c:\Users\alarabi\Desktop\my_bot\PC_Remote_Pro.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "c:\Users\alarabi\Desktop\my_bot\PC_Remote_Pro.jpg"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\PC_Remote_Pro.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\PC_Remote_Pro.ico"; Tasks: desktopicon

[Registry]
; Register automatic startup if selected during setup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PCRemoteProController"; ValueData: """{app}\{#MyAppExeName}"" --silent"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
