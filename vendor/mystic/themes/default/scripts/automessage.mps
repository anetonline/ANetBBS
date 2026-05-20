// AUTOMESSAGE : Basic auto-message MPL for Mystic BBS
// ===================================================
//
// Options:    <NONE>  Show message and prompt to change message
//               SHOW  Only show message, then show a pause prompt
//        SHOWNOPAUSE  Same as SHOW but does not send PausePrompt
//             CHANGE  Only change message and nothing else
//
// The auto message has been designed so that you can just execute it with no
// options, and it will display and allow the user to change the message.  It
// also gives you the show and change options separately, so you can build
// your own Automessage menu with the menu system, if you choose to, and that
// will give you some additional security options and display options.
//
// Example:
//
//   Menu Command: GX (Execute MPL)
//  Optional Data: automessage show
//
// Prompts can be changed below.  The header prompt can have &1 = Msg by,
// &2 = Msg date, &3 = Msg time.

Uses
  USER;

Const
  MaxLines     = 6;                  // up to 25
  MaxColumns   = 79;                 // up to 79

  // Sent as the auto message header
  HeaderPrompt = '|16|CL|11Auto-Message by |15|&1 |11on |15|&2:|CR|03';

  // Sent before each line of text of the actual auto message
  MiddlePrompt = '';

  // Sent when there is no defined auto message yet
  NonePrompt   = 'An auto message has not been set yet.';

  // Used as the prompt prompt when using the SHOW command
  PausePrompt  = '|CR|PA';

  // Prompt used to give option to change the message or continue
  ChangePrompt = '|CR|01[|11C|01]|09hange Message, or |01[|11ENTER|01]|09 to Continue: ';

  // If its the User who wrote the auto message, they can edit this prompt
  // be be displayed instead of ChangePrompt
  OwnerPrompt  = '|CR|01[|11C|01]|09hange Message, |01[|11E|01]|09dit or |01[|11ENTER|01]|09 to Continue: ';

/////////////////////////////////////////////////////////////////////////////
//                  DO NOT EDIT EXCEPT FOR THE VALUES ABOVE                //
/////////////////////////////////////////////////////////////////////////////

Var
  Text      : Array[1..25] of String[79];
  TextDate  : String[8];
  TextTime  : String[8];
  TextFrom  : String[40];
  TextLines : Byte;

Procedure ReadData;
Var
  F : File;
Begin
  fAssign (F, JustPath(ProgName) + 'automessage.dat', 66);
  fReset  (F);

  TextFrom  := 'Unknown';
  TextDate  := DateStr(DateTime, 1);
  TextTime  := TimeStr(DateTime, False);
  TextLines := 0;

  If IoResult = 0 Then Begin
    For TextLines := 1 to 25 Do
      Text[TextLines] := '';

    TextLines := 0;

    fReadLn(F, TextFrom);
    fReadLn(F, TextDate);
    fReadLn(F, TextTime);

    While Not fEOF(F) Do Begin
      TextLines := TextLines + 1;

      fReadLn (F, Text[TextLines]);
    End;

    fClose(F);
  End;

  SetPromptInfo(1, TextFrom);
  SetPromptInfo(2, TextDate);
  SetPromptInfo(3, TextTime);
End;

Procedure Show (DoPause: Boolean);
Var
  Count : Byte;
Begin
  WriteLn (HeaderPrompt);

  If TextLines = 0 Then
    WriteLn(NonePrompt)
  Else
    For Count := 1 to TextLines Do
      WriteLn (MiddlePrompt + Text[Count]);

  If DoPause Then
    Write(PausePrompt);
End;

Procedure Change;
Var
  Prompt  : String;
  Cmds    : String;
  Ch      : Char;
  Lines   : Integer;
  Subject : String = 'Auto-Message';
  Count   : Word;
  F       : File;
Begin
  GetThisUser;

  If Upper(TextFrom) <> 'ANONYMOUS' and Upper(TextFrom) <> 'UNKNOWN' and TextFrom = UserAlias Then Begin
    Prompt := OwnerPrompt;
    Cmds   := 'CQE' + #13;
  End Else Begin
    Prompt := ChangePrompt
    Cmds   := 'CQ' + #13;
  End;

  Repeat
    Write (Prompt);

    Ch := OneKey(Cmds, True);

    Case Ch of
      'Q',
      #13 : Break;
      'E',
      'C' : Begin
              If Ch = 'E' Then Begin
                For Count := 1 to TextLines Do
                  MsgEditSet (Count, Text[Count]);

                Lines := TextLines;
              End Else
                Lines := 0;

              SetPromptInfo(1, Subject);

              If MsgEditor (0, Lines, MaxColumns, MaxLines, False, 'msg_editor', Subject) Then Begin
                fAssign  (F, JustPath(ProgName) + 'automessage.dat', 66);
                fReWrite (F);

                fWriteLn (F, UserAlias);
                fWriteLn (F, DateStr(DateTime, 1));
                fWriteLn (F, TimeStr(DateTime, False));

                For Count := 1 to Lines Do
                  fWriteLn (F, MsgEditGet(Count));

                fClose (F);
              End;

              ReadData;
              Show(False);
            End;
    End;
  Until False;
End;

Begin
  ReadData;

  Case Upper(ParamStr(1)) of
    'SHOWNOPAUSE' : Show(False);
    'SHOW'        : Show(True);
    'CHANGE'      : Change;
  Else
    Show(False);
    Change;
  End;
End.
