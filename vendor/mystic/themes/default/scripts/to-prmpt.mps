// .-------------------------.
// | TO-PRMPT.MPS : UPDATE 3 |===============================================
// `-------------------------'
//
//  This mod is a lightbar prompt replacement for standard message reading,
//  standard file listing, standard e-mail reader, and the pause y/n/c
//  prompt.
//
//  See installation section for more details.
//
//  -------------------------------------------------------------------------
//  BACKGROUND
//  -------------------------------------------------------------------------
//
//  This mod is was originally written for use with Mystic BBS v1.07.3 by
//  Testoverride, based on some demo MPL code written by g00r00.
//
//  It has been updated for Mystic BBS 1.10+ by g00r00, and released without
//  Testoverride's assistance.  This is not an intentional thing, but TO has
//  been missing lately, so given the circumstances that it was based off of
//  g00r00's code, we feel it's okay to go forward with this release.
//
//  You are free to modify and do whatever you'd like to with this code, but
//  please if you do make significant changes please let the original authors
//  know so that we can include it into our release if it is worthwhile.
//
//  The original authors contact info follows:
//
//    Testoverride - testoverride@comcast.net (unconfirmed)
//          g00r00 - mysticbbs@gmail.com
//
//  --------------------------------------------------------------------------
//  INSTALLATION
//  --------------------------------------------------------------------------
//
//  Replace the following prompts with the following data if you want to
//  replace them with TO-PRMPT lightbar prompt functionality (exclude quotes):
//
//     Message reading prompts: Set Prompt #116 to "!to-prmpt MESSAGE"
//                              Set Prompt #213 to "!to-prmpt MESSAGE"
//     E-mail reading prompt  : Set Prompt #115 to "!to-prmpt EMAIL"
//     File Listing prompt    : Set Prompt #044 to "!to-prmpt FILE"
//     YNC Pause prompt       : Set Prompt #132 to "!to-prmpt PAUSE"
//     Msg Editor prompt      : Set Prompt #354 to "!to-prmpt EDITOR"
//
//  When you have changed the prompts, you must compile them again with
//  MAKETHEME, or if you changed them inside the internal prompt editor, the
//  theme prompts file will usually be compiled for you.
//
//  --------------------------------------------------------------------------
//  New updates for 1.10:
//  --------------------------------------------------------------------------
//
//    - Converted to new MPL 1.10
//    - Changed the s255 ACS check to use the message owner MCI code instead
//    - Added the 'H' command to the message reader prompt (set lastread)
//    - Added the 'M' command to the message reader prompt (move message)
//    - Added the 'F' command to the message reader prompt (forward)
//    - Added the FS editor prompt option
//    - Some conversions of IF statements to CASE statements for code clarity
//
// ===========================================================================

Var
  Selection : Byte;

Function EditPromptMenu : Byte
Var
  Ch   : Char;
  Done : Boolean;
  Bar  : Byte;
  Cmd  : Array[1..7] of String[80];
  Xpos : Array[1..7] of String[80];
Begin

  Done := False
  Bar  := 1

  Xpos[1] := '|[X14'
  Xpos[2] := '|[X20'
  Xpos[3] := '|[X27'
  Xpos[4] := '|[X34'
  Xpos[5] := '|[X44'
  Xpos[6] := '|[X52'
  Xpos[7] := '|[X59'

  Cmd[1] := ' |15S|07ave '
  Cmd[2] := ' |15Q|07uote '
  Cmd[3] := ' |15A|07bort '
  Cmd[4] := ' |15C|07ontinue '
  Cmd[5] := ' |15U|07pload '
  Cmd[6] := ' |15T|07itle '
  Cmd[7] := ' |15H|07elp '

  Repeat
    If Graphics > 0 Then
      Write ('|15|17' + Xpos[Bar]+stripmci(Cmd[Bar]) + '|00|16');

    Ch := ReadKey

    If Graphics > 0 and IsArrow Then Begin
      Write (Xpos[bar] + Cmd[Bar] + '|00|16');
      If Ord(Ch) = 75 Then Begin
        If Bar > 1 Then
          Bar := Bar - 1
      End Else
      If Ord(Ch) = 77 Then Begin
        If Bar < 7 Then
          Bar := Bar + 1
      End
    End Else
      If Ch = #13 and Graphics > 0 Then Begin
        EditPromptMenu := Bar
        Done           := True
      End Else
      If Upper(Ch) = 'S' Then Begin
        EditPromptMenu := 1
        Done           := True
      End Else
      If Upper(Ch) = 'Q' Then Begin
        EditPromptMenu := 2
        Done           := True
      End Else
      If Upper(Ch) = 'A' Then Begin
        EditPromptMenu := 3
        Done           := True
      End Else
      If Upper(Ch) = 'C' Then Begin
        EditPromptMenu := 4
        Done           := True
      End Else
      If Upper(Ch) = 'U' Then Begin
        EditPromptMenu := 5
        Done           := True
      End Else
      If Upper(Ch) = 'T' Then Begin
        EditPromptMenu := 6
        Done           := True
      End Else
      If Upper(Ch) = 'H' Then Begin
        EditPromptMenu := 7
        Done           := True
      End
  Until Done
End

Function FPromptMenu : Byte
Var
  Ch   : Char;
  Done : Boolean;
  Bar  : Byte;
  Cmd  : Array[1..5] of String[80];
  Xpos : Array[1..5] of String[80];
Begin

  Done := False
  Bar  := 1

  Xpos[1] := '|[X38'
  Xpos[2] := '|[X44'
  Xpos[3] := '|[X54'
  Xpos[4] := '|[X60'
  Xpos[5] := '|[X66'

  Cmd[1] := ' |15N|07ext '
  Cmd[2] := ' |15P|07revious '
  Cmd[3] := ' |15F|07lag '
  Cmd[4] := ' |15V|07iew '
  Cmd[5] := ' |15Q|07uit '

  Repeat
    If Graphics > 0 Then
      Write ('|15|17' + Xpos[Bar]+stripmci(Cmd[Bar]) + '|00|16');

    Ch := ReadKey

    If Graphics > 0 and IsArrow Then Begin
      Write (Xpos[bar] + Cmd[Bar] + '|00|16');
      If Ord(Ch) = 75 Then Begin
        If Bar > 1 Then
          Bar := Bar - 1
      End Else
      If Ord(Ch) = 77 Then Begin
        If Bar < 5 Then
          Bar := Bar + 1
      End
    End Else
      If Ch = #13 and Graphics > 0 Then Begin
        FPromptMenu := Bar
        Done        := True
      End Else
      If Upper(Ch) = 'N' Then Begin
        FPromptMenu := 1
        Done        := True
      End Else
      If Upper(Ch) = 'P' Then Begin
        FPromptMenu := 2
        Done        := True
      End Else
      If Upper(Ch) = 'F' Then Begin
        FPromptMenu := 3
        Done        := True
      End Else
      If Upper(Ch) = 'V' Then Begin
        FPromptMenu := 4
        Done        := True
      End Else
      If Upper(Ch) = 'Q' Then Begin
        FPromptMenu := 5
        Done        := True
      End
  Until Done
End

Function EPromptMenu : Byte
Var
  Ch   : Char;
  Done : Boolean;
  Bar  : Byte;
  Cmd  : Array[1..7] of String[80];
  Xpos : Array[1..7] of String[80];
Begin

  Done := False
  Bar  := 1

  Xpos[1] := '|[X22'
  Xpos[2] := '|[X28'
  Xpos[3] := '|[X38'
  Xpos[4] := '|[X45'
  Xpos[5] := '|[X52'
  Xpos[6] := '|[X58'
  Xpos[7] := '|[X66'

  Cmd[1] := ' |15N|07ext '
  Cmd[2] := ' |15P|07revious '
  Cmd[3] := ' |15A|07gain '
  Cmd[4] := ' |15R|07eply '
  Cmd[5] := ' |15J|07ump '
  Cmd[6] := ' |15D|07elete '
  Cmd[7] := ' |15Q|07uit '

  Repeat
    If Graphics > 0 Then
      Write ('|15|17' + Xpos[bar]+stripmci(Cmd[Bar]) + '|00|16')

    Ch := ReadKey

    If Graphics > 0 and IsArrow Then Begin
      Write (Xpos[bar]+Cmd[Bar] + '|00|16')
      If Ord(Ch) = 75 Then Begin
        If Bar > 1 Then Bar := Bar - 1
      End Else
      If Ord(Ch) = 77 Then Begin
        If Bar < 7 Then Bar := Bar + 1
      End
    End Else Begin
      If Ch = Chr(13) and Graphics > 0 Then Begin
        EPromptMenu := Bar
        Done     := True
      End Else
      If Upper(Ch) = 'N' Then Begin
        EPromptMenu := 1
        Done     := True
      End Else
      If Upper(Ch) = 'P' Then Begin
        EPromptMenu := 2
        Done     := True
      End Else
      If Upper(Ch) = 'A' Then Begin
        EPromptMenu := 3
        Done     := True
      End Else
      If Upper(Ch) = 'R' Then Begin
        EPromptMenu := 4
        Done     := True
      End Else
      If Upper(Ch) = 'J' Then Begin
        EPromptMenu := 5
        Done     := True
      End Else
      If Upper(Ch) = 'D' Then Begin
        EPromptMenu := 6
        Done     := True
      End Else
      If Upper(Ch) = 'Q' Then Begin
        EPromptMenu := 7
        Done     := True
      End Else
      If Upper(Ch) = 'X' Then Begin
        stuffkey(ch)
        Done     := True
      End Else
      If Upper(Ch) = '?' Then Begin
        stuffkey(ch)
        Done     := True
      End Else
      If Upper(Ch) = 'L' Then Begin
        stuffkey(ch)
        Done     := True
      End
    End
  Until Done
End

Function MPromptMenu : Byte;
Var
  Done : Boolean;
  Ch   : Char
  Bar  : Byte
  Cmd  : Array[1..6] of String[80]
  Xpos : Array[1..6] of String[80]
Begin
  Bar := 1;

  Xpos[1] := '|[X36'
  Xpos[2] := '|[X42'
  Xpos[3] := '|[X52'
  Xpos[4] := '|[X59'
  Xpos[5] := '|[X66'
  Xpos[6] := '|[X72'

  Cmd[1] := ' |15N|07ext|00|16 '
  Cmd[2] := ' |15P|07revious|00|16 '
  Cmd[3] := ' |15A|07gain|00|16 '
  Cmd[4] := ' |15R|07eply|00|16 '
  Cmd[5] := ' |15J|07ump|00|16 '
  Cmd[6] := ' |15Q|07uit|00|16 '

  Repeat
    If Graphics > 0 Then
      Write ('|15|17' + Xpos[bar]+stripmci(Cmd[Bar]) + '|00|16');

    Ch := Upper(ReadKey);

    If Graphics > 0 and IsArrow Then Begin
      Write (Xpos[bar]+Cmd[Bar] + '|00|16');

      Case Ch of
        #75 : If Bar > 1 Then Bar := Bar - 1;
        #77 : If Bar < 6 Then Bar := Bar + 1;
      End
    End Else Begin
      Case Ch of
        #13 : If Graphics > 0 Then Begin
                MPromptMenu := Bar;
                Done        := True;
              End;
        'N' : Begin
                MPromptMenu := 1;
                Done     := True;
              End;
        'P' : Begin
                MPromptMenu := 2;
                Done     := True;
              End;
        'A' : Begin
                MPromptMenu := 3;
                Done     := True;
              End;
        'R' : Begin
                MPromptMenu := 4;
                Done     := True;
              End;
        'J' : Begin
                MPromptMenu := 5;
                Done     := True;
              End;
        'Q' : Begin
                MPromptMenu := 6;
                Done     := True;
              End;
      Else
        If (Pos(Ch, 'MEFD') > 0 And ACS('OM')) OR (Pos(Ch, 'X?[]HITGL') > 0) Then Begin
          StuffKey(Ch);
          Break;
        End;
      End;
    End;
  Until Done;
End;

Function PPromptMenu : Byte
Var
  Ch   : Char
  Done : Boolean
  Bar  : Byte
  Cmd  : Array[1..3] of String[80];
  Xpos : Array[1..3] of String[80];
Begin
  Done := False
  Bar  := 1

  Xpos[1] := '|[X21'
  Xpos[2] := '|[X26'
  Xpos[3] := '|[X30'

  Cmd[1] := ' |15Y|07es '
  Cmd[2] := ' |15N|07o '
  Cmd[3] := ' |15C|07ontinuous '

  Repeat
    If Graphics > 0 Then
      Write ('|15|17' + XPos[Bar] + StripMCI(Cmd[Bar]) + '|00|16')

    Ch := ReadKey

    If Graphics > 0 and IsArrow Then Begin
      Write (XPos[Bar] + Cmd[Bar] + '|00|16')
      If Ord(Ch) = 75 Then Begin
        If Bar > 1 Then Bar := Bar - 1
      End Else
      If Ord(Ch) = 77 Then Begin
        If Bar < 3 Then Bar := Bar + 1
      End
    End Else
      If Ch = #13 and Graphics > 0 Then Begin
        PPromptMenu := Bar
        Done     := True
      End Else
      If Upper(Ch) = 'Y' Then Begin
        PPromptMenu := 1
        Done     := True
      End Else
      If Upper(Ch) = 'N' Then Begin
        PPromptMenu := 2
        Done     := True
      End Else
      If Upper(Ch) = 'C' Then Begin
        PPromptMenu := 3
        Done     := True
      End
  Until Done
End

Procedure MESSAGE
Begin
  Write ('|CR|08>>|07 Reading messages |15|$L04|&5 |07of |15|$R04|&6 |08//  |15N|09ext  |15P|07revious  |15A|07gain  |15R|07eply  |15J|07ump  |15Q|07uit |00')

  Selection := MPromptMenu

  MoveX(1);
  TextColor(7);
  ClrEOL;

  Case Selection of
    1 : stuffkey('N');
    2 : stuffkey('P');
    3 : stuffkey('A');
    4 : stuffkey('R');
    5 : stuffkey('J');
    6 : stuffkey('Q');
  End;
End

Procedure DOPAUSE
Var
  SavedX : Byte;
Begin
  Write ('|08>> |07Paused |08- |07More|08 //  |15Y|09es  |15N|07o  |15C|07ontinuous |00');

  SavedX := WhereX;

  Selection := PPromptMenu

  If Selection = 1 Then
    stuffkey('Y')
  Else
  If Selection = 2 Then
    stuffkey('N')
  Else
  If Selection = 3 Then
    stuffkey('C')

  Write('|[X' + PadLT(Int2Str(SavedX), 2, '0'));
End

Procedure Email
Begin
  Write ('|CR|08>> |07Reading e-mail |08//  |15N|09ext  |15P|07revious  |15A|07gain  |15R|07eply  |15J|07ump  |15D|07elete  |15Q|07uit |00')

  Selection := EPromptMenu

  If Selection = 1 Then
    stuffkey('N')
  Else
  If Selection = 2 Then
    stuffkey('P')
  Else
  If Selection = 3 Then
    stuffkey('A')
  Else
  If Selection = 4 Then
    stuffkey('R')
  Else
  If Selection = 5 Then
    stuffkey('J')
  Else
  If Selection = 6 Then
    stuffkey('D')
  Else
  If Selection = 7 Then
    stuffkey('Q')
End

Procedure Editor;
Begin
  Write ('|CR|08<< |07Editor |08//  |15S|07ave  |15Q|07uote  |15A|07bort  |15C|07ontinue  |15U|07pload  |15T|07itle  |15H|07elp  |08>>');

  Case EditPromptMenu of
    1 : stuffKey('S');
    2 : stuffKey('Q');
    3 : stuffKey('A');
    4 : stuffKey('C');
    5 : stuffKey('U');
    6 : stuffKey('T');
    7 : stuffKey('H');
  End;
End;

Procedure File
Begin
  Write ('|CR|08[|07|$R31|FB|08] |08//  |15N|07ext  |15P|07revious  |15F|07lag  |15V|07iew  |15Q|07uit  |08>>')

  Selection := FPromptMenu

  If Selection = 1 Then
    stuffkey('N')
  Else
  If Selection = 2 Then
    stuffkey('P')
  Else
  If Selection = 3 Then
    stuffkey('F')
  Else
  If Selection = 4 Then
    stuffkey('V')
  Else
  If Selection = 5 Then
    stuffkey('Q')
End

Const
  FailStr = '|CRUSAGE: to-prmpt [ MESSAGE | FILE | EMAIL | PAUSE | EDITOR ]|CR|CR|PA';

Begin
  AllowArrow := True;

  If ParamCount < 1 Then
    WriteLn(FailStr)
  Else
    Case Upper(ParamStr(1)) of
      'MESSAGE': MESSAGE;
      'FILE'   : FILE;
      'EMAIL'  : EMAIL;
      'PAUSE'  : DOPAUSE;
      'EDITOR' : EDITOR;
    Else
      WriteLn(FailStr);
    End;
End.
