// ONLYONCE.MPS: Display a file only if it has been updated since the users
//               last login.  Written by g00r00
// Usage:
//    Menu command: GX
//            Data: onlyonce myfile
//
// The above example will display myfile.XXX from current text directory
// only if it has been updated since the users last login

Uses
  CFG,
  USER;

Var
  FN : String;
Begin
  GetThisUser;

  FN := JustFileName(ParamStr(1));

  If Pos(PathChar, ParamStr(1)) = 0 Then
    FN := CfgTextPath + FN;

  FindFirst (FN + '.*', 0);

  While DosError = 0 Do Begin
    If DirTime > DateU2D(UserLastOn) Then Begin
      DispFile(FN);

      Break;
    End;

    FindNext
  End;

  FindClose;
End.
