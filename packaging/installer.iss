; EvoBlue Video MCP user-level installer (P7).
; Contract: docs/INSTALLER_RELEASE_CONTRACT.md section 2 (layout, AppId,
; autostart, uninstall semantics). Built by scripts/build_installer.py which
; injects /DAppVersion and /DSourceDir; do not hardcode a version here.

#define MyAppName "EvoBlue Video MCP"
#ifndef AppVersion
#define AppVersion "0.0.0-dev"
#endif
#ifndef SourceDir
#define SourceDir "..\dist\evoblue-video-mcp-full"
#endif

[Setup]
AppId={{55355472-adb9-4258-b2fe-34c2af302239}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher=EvoBlue
DefaultDirName={localappdata}\Programs\EvoBlue Video MCP
PrivilegesRequired=lowest
; Engine holds this exact literal mutex while running (runtime/singleton.py);
; under PrivilegesRequired=lowest Inno probes it with an implicit Local\ prefix.
AppMutex=EvoBlueVideoMCP-Engine
OutputDir=..\dist
OutputBaseFilename=EvoBlueVideoMCP-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\evoblue-engine-full.exe
; the running-engine case is handled by AppMutex; do not restart apps
CloseApplications=no

[Tasks]
Name: "autostart"; \
  Description: "登录 Windows 时自动启动 EvoBlue Engine（推荐）"; \
  GroupDescription: "自启动:"; \
  Flags: checkedonce
Name: "desktopicon"; \
  Description: "创建桌面快捷方式"; \
  GroupDescription: "快捷方式:"; \
  Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\EvoBlue Video MCP"; Filename: "{app}\evoblue-engine-full.exe"
Name: "{autodesktop}\EvoBlue Video MCP"; Filename: "{app}\evoblue-engine-full.exe"; Tasks: desktopicon

[Registry]
; Quoted path: the install root contains spaces; an unquoted Run value with
; spaces silently mis-launches. uninsdeletevalue removes the autostart entry
; with the program.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "EvoBlue Video MCP"; \
  ValueData: """{app}\evoblue-engine-full.exe"""; \
  Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\evoblue-engine-full.exe"; \
  Description: "启动 EvoBlue Video MCP"; \
  Flags: nowait postinstall skipifsilent

[Code]
// Record version + the resolved data-dir literal at install time; the
// uninstall data step reads this file instead of re-deriving the path, so
// the delete set is always the exact directory the engine used.
procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: string;
begin
  if CurStep = ssPostInstall then
  begin
    DataDir := ExpandConstant('{localappdata}\EvoBlue\EvoBlue Video MCP');
    SaveStringToFile(
      ExpandConstant('{app}\install-version.txt'),
      'version={#AppVersion}' + #13#10 + 'data_dir=' + DataDir + #13#10,
      False);
  end;
end;

// Optional data wipe at uninstall: default is KEEP (No, MB_DEFBUTTON2), and
// silent uninstalls (/SUPPRESSMSGBOXES) take the default - data is never
// removed without an explicit interactive Yes (contract section 2).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir, VersionFile: string;
  Lines: TStringList;
  I: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    // install-version.txt is written by [Code], so it is NOT in the uninstall
    // log: it must be removed unconditionally, or even a silent uninstall
    // (which takes the default No below) would leave the install dir behind.
    DataDir := '';
    VersionFile := ExpandConstant('{app}\install-version.txt');
    if FileExists(VersionFile) then
    begin
      Lines := TStringList.Create;
      try
        Lines.LoadFromFile(VersionFile);
        for I := 0 to Lines.Count - 1 do
          if Pos('data_dir=', Lines[I]) = 1 then
            DataDir := Copy(Lines[I], 10, MaxInt);
      finally
        Lines.Free;
      end;
      DeleteFile(VersionFile);
    end;
    // Optional data wipe: default is KEEP (No, MB_DEFBUTTON2); silent
    // uninstalls take the default - user data is never removed without an
    // explicit interactive Yes (contract section 2).
    if SuppressibleMsgBox(
        '是否同时删除个人数据（分析报告、数据库、已安装的语音模型）？' #13#10 #13#10 +
        '选「否」只移除程序，个人数据保留。',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    begin
      if (DataDir <> '') and DirExists(DataDir) then
      begin
        DelTree(DataDir, True, True, True);
        SuppressibleMsgBox('个人数据已删除。', mbInformation, MB_OK, IDOK);
      end;
    end;
  end;
end;
