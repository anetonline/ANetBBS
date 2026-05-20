// Auto create MPL for RLOGIN auto-creation example
// ================================================
//
// This would be renamed to connect.mps which is executed as soon as a
// connection comes in, even before graphics detection.
//
// By default this script will create a user automatically if AutoCreate is
// set to TRUE below, or just push them immediately through to the new user
// application if set to FALSE.
//
// Security below defines the level used for the created user.  Ideally you
// would probably want to create them with a specific security level and then
// you could redirect them to their own menu system which would be completely
// independant from the BBS.

Const
  EnableRLOGIN = True;     // If True, process RLOGIN users for autocreate
  EnableSSH    = False;    // If True, process SSH users for autocreate
  AutoCreate   = True;     // Set True to autocreate or False to send to new
                           // user application.
  StartMenu    = '';       // Menu to start users at (Blank for default)
  Security     = 0;        // Security level to use for created users or 0
                           // to use the default new user security level
Begin
  If ((ServerType = 1 and EnableRLOGIN) or (ServerType = 2 and EnableSSH)) and
     (UserLoginName <> '') and (UserLoginPW <> '') Then Begin

    // This is connection from RLOGIN or SSH, so lets see if the user exists.
    // Other manipulation or validation of the User ID or password could be
    // done here too as needed.

    If Not IsUser(UserLoginName) Then Begin

      // User does not exist, so we can either set "UserLoginNew" to true to
      // push the user directly to the new user application, or we could
      // create the user and automatically push them through.

      If AutoCreate Then Begin
        Var Cmd : String = 'mystic -newuser handle="' + UserLoginName + '" "pass=' + UserLoginPW + '"';

        If (Security > 0) and (Security < 250) Then
          Cmd := Cmd + ' level=' + Int2Str(Security);

        If StartMenu <> '' Then
          Cmd := Cmd + ' menu=' + StartMenu;

        If OSType = 1 or OSType = 2 Then
          Cmd := './' + Cmd + ' /dev/null 2>&1';

        SysopLog ('Auto creating user via RLOGIN: ' + UserLoginName);

        MenuCmd('DD', Cmd);
      End Else
        UserLoginNew := True;
    End;
  End;
End.
