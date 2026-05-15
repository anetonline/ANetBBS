// ---------------------------------------------------------------------------
// MPLDEMO.MPS : Mystic Programming Language (MPL) Demonstration Program
// ---------------------------------------------------------------------------
// Written by g00r00 for Mystic BBS Version 1.07.  Feel free to do whatever
// you want with this source code!  This is just something quick I put
// together.  Updated for Mystic 1.10
// ---------------------------------------------------------------------------

USES CFG;
USES USER;

Procedure FadeWrite (X, Y: Byte; S: String);
Begin
  GotoXY (X, Y);
  Write  ('|08' + S);
  BufFlush;
  Delay  (250);
  
  GotoXY (X, Y);
  Write  ('|07' + S);
  BufFlush;
  Delay  (250);
  
  GotoXY (X, Y);
  Write  ('|15' + S);
  BufFlush;
  Delay  (250);
  
  GotoXY (X, Y);
  Write  ('|07' + S);
  BufFlush;
End;

Procedure Draw_M (X: Byte);
Begin
  GotoXY (X - 1, 9);
  Write  (' |17|09²|16|01ÛÛÛßÛßÛ');
  GotoXY (X - 1, 10);
  Write  (' |17|09±|16|01ÛÛÛ   Û');
  GotoXY (X - 1, 11);
  Write  (' |01ÛÛÛÛ   Û');

  BufFlush;
End;

Procedure Draw_P (Y: Byte)
Begin
  GotoXY (39, Y - 1);
  Write  ('      ');
  GotoXY (39, Y);
  Write  ('|09|17²|01|16ÛÛÛßÛ');
  GotoXY (39, Y + 1);
  Write  ('|09|17±|01|16ÛÛÛÜÛ');
  GotoXY (39, Y + 2);
  Write  ('ÛÛÛÛ');

  BufFlush;
End;

Procedure Draw_L (X : Byte)
Begin
  GotoXY (X, 9);
  Write  ('|09|17²|01|16ÛÛÛ ');
  GotoXY (X, 10);
  Write  ('|09|17±|01|16ÛÛÛ ');
  GotoXY (X, 11);
  Write  ('ÛÛÛÛÜÛ ');

  BufFlush;
End;

Procedure Draw_Animated_Intro;
Var
  Count : Byte;
Begin
  ClrScr;

  For Count := 2 to 30 Do Begin
    Draw_M(Count);
    Delay(5);
  End;

  For Count := 1 to 9 Do Begin
    Draw_P(Count);
    Delay(20);
  End;

  For Count :=  74 DownTo 46 Do Begin
    Draw_L(Count);
    Delay(5);
  End;

  FadeWrite (24, 13, 'The Mystic BBS Programming Language');
  FadeWrite (34, 15, 'Press Any Key');
  Write     ('|PN');
End;

Procedure DrawHeader;
Begin
  WriteLn ('|CL');
  WriteLn ('       |09|17²|01|16ÛÛÛßÛßÛ            |09|17²|01|16ÛÛÛßÛ                    |09|17²|01|16ÛÛÛ');
  WriteLn ('       |09|17±|01|16ÛÛÛ   Û            |09|17±|01|16ÛÛÛÜÛ                    |09|17±|01|16ÛÛÛ');
  WriteLn ('       ÛÛÛÛ   Û |11y s t i c  |01ÛÛÛÛ |11r o g r a m m i n g  |01ÛÛÛÛÜÛ |11a n g u a g e');
  WriteLn ('       |09ÄÄÄÄÄÄ |01ß |09ÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄÄ|07');
  WriteLn ('');
End;

Procedure InputDemo;
Var
  Str : String;
Begin
  DrawHeader;

  WriteLn ('       This demonstrates some of the types of input functions which');
  WriteLn ('       are available within the Mystic Programming Language.|CR');
  Write   ('       |09Regular input  ') Str := Input(30, 30, 11, '');
  Write   ('       |09Caps input     ') Str := Input(30, 30, 12, '');
  Write   ('       |09Proper input   ') Str := Input(30, 30, 13, '');
  Write   ('       |09Phone input    ') Str := Input(12, 12, 14, '');
  Write   ('       |09Date input     ') Str := Input(8,  8,  15, '');
  Write   ('       |09Password input ') Str := Input(20, 20, 16, '');

  WriteLn ('|CR       |07Text can also be pushed into the input buffer:|CR');
  Write   ('|09       Regular Input  ') Str := Input(30, 30, 11, 'Default Text');
  WriteLn ('|CR       |07Input can be used without the input field:|CR');
  Write   ('|09       Regular Input  |11') Str := Input(30, 30, 1, 'Default Text');

  DrawHeader;

  WriteLn ('|07       The input functions also make full use of ANSI editing.  Arrow');
  WriteLn ('       keys can be used to move around the field, as well as the HOME,');
  WriteLn ('       END, DEL, and CTRL-Y keys.  Up arrow restores previously entered text!');
  WriteLn ('|CR       Text longer than the input box can be entered in both ANSI and');
  WriteLn ('       non-ansi terminal modes.  For example: Type more than 30 characters');
  WriteLn ('       below, while experimenting with the other ANSI editing functions');
  WriteLn ('       mentioned above.');

  Write   ('|CR       |09Scroll Input   ') Str := Input(30, 255, 11, '');
  Write   ('|CR       |PA');
End;

Procedure UserListingHeader;
Begin
  DrawHeader;

  WriteLn ('       User Name                  Location                   SecLev   Sex');
  WriteLn ('       ------------------------------------------------------------------');
End;

Procedure UserListing;
Var
  Count : Word = 1;
Begin
  UserListingHeader;

  While GetUser(Count) Do Begin
    WriteLn ('       ' + PadRT(UserAlias, 25, ' ') + '  ' + PadRT(UserAddress, 25, ' ') + '  ' +
                         PadLT(Int2Str(UserSec), 6, ' ') + '    ' + UserSex);

    If Count % 10 = 0 Then Begin
      Write ('       Continue? (Y/N): ');

      Case OneKey('YN', True) of
        'Y' : UserListingHeader;
        'N' : Break;
      End;
    End;

    Count := Count + 1;
  End;

  WriteLn ('|CR       Total of |15' + Int2Str(Count - 1) + ' |07users listed.|CR');
  Write   ('       |PA');
End;

Procedure PlayNumberGame;
Var
  GuessNum : Byte;
  Answer,
  Temp     : Integer;
Begin
  DrawHeader;

  WriteLn ('       |12Choose a number between 1 and 1000.  You have 10 guesses.')

  GuessNum := 0;
  Answer   := Random(999) + 1;

  Repeat
    GuessNum := GuessNum + 1;

    Write ('|CR|03       Guess #' + Int2Str(GuessNum) + ': ');

    Temp := Str2Int(Input(4, 4, 12, ''))

    If Temp > Answer Then
      WriteLn ('|CR       |07The number is less than ' + Int2Str(Temp))
    Else
    If Temp < Answer Then
      WriteLn ('|CR       |07The number is greater than ' + Int2Str(Temp))
    Else
      GuessNum := 10;
  Until GuessNum = 10;

  If Temp = Answer Then
    WriteLn ('|CR       |12You won!  The number was: ' + Int2Str(Answer))
  Else
    WriteLn ('|CR       |12You lost.  The number was: ' + Int2Str(Answer));

  Write ('|CR       |PA');
End;

Function MainMenu : Byte;
Var
  Ch   : Char;
  Done : Boolean = False;
  Bar  : Byte = 1;
  Ops  : Array[1..4] of String[20];
Begin
  DrawHeader;

  WriteLn ('       The Mystic BBS Programming Language (MPL for short) allows for the');
  WriteLn ('       ultimate in  flexibility.   With it''s  Pascal-like syntax, the MPL');
  WriteLn ('       provides an  easy  and flexible way to  modify internal Mystic BBS');
  WriteLn ('       functions, or  even create  your own online  games!  Check it out!');
  WriteLn ('|09|CR       |$D66Ä|CR');

  WriteLn ('       |09(|101|09)  |03Input demo    |08-> |07See some example input functions');
  WriteLn ('       |09(|102|09)  |03User listing  |08-> |07See a list of user accounts');
  WriteLn ('       |09(|103|09)  |03Number game   |08-> |07Play a simple number game');
  WriteLn ('       |09(|10Q|09)  |03Quit Demo     |08-> |07Return to the BBS menu');

  WriteLn ('|09|CR       |$D66Ä');
  Write   ('       |07Select an option with arrow keys, or enter option number ');

  Ops[1] := 'Input demo';
  Ops[2] := 'User listing';
  Ops[3] := 'Number game';
  Ops[4] := 'Quit Demo';

  Repeat
    If Graphics > 0 Then Begin
      GotoXY (12, 13 + Bar);
      Write  ('|01|23 ' + Ops[Bar] + ' |16');
    End;

    Ch := ReadKey;

    If Graphics > 0 and IsArrow Then Begin
      GotoXY (12, 13 + Bar);
      Write  ('|03 ' + Ops[Bar] + ' ');

      Case Ch of
        #72 : If Bar > 1 Then Bar := Bar - 1;
        #80 : If Bar < 4 Then Bar := Bar + 1;
      End;
    End Else
      Case Upper(Ch) of
        #13 : If Graphics > 0 Then Begin
                MainMenu := Bar;
                Done     := True;
              End;
        'Q' : Begin
                MainMenu := 4;
                Done     := True;
              End;
      Else
        If Str2Int(Ch) > 0 And Str2Int(Ch) < 4 Then Begin
          MainMenu := Str2Int(Ch);
          Done     := True;
        End;
      End;
  Until Done;
End;

Begin
  Draw_Animated_Intro;

  Repeat
    Case MainMenu of
      1 : InputDemo;
      2 : UserListing;
      3 : PlayNumberGame;
      4 : Break;
    End;
  Until False;

  GotoXY (1, 20);
End.
