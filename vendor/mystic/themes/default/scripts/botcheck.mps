// BOTCHECK.MPS: Example script to force users to immediately press ESCAPE
//               twice upon connection within 15 seconds or else their
//               connection will be closed.
//
// To install: Copy this as "connect.mps" in your theme's script directory
// and then use MPLC to compile it (mplc -T will compile all theme scripts)

Const
  Message = 'Press ESCAPE twice to continue: ';
  TimeOut = 15;

Var
  TimeStart : LongInt = TimerMS;
  TimeEnd   : LongInt = TimeStart + (TimeOut * 1000);
  EscCount  : Byte = 0;
  Last      : LongInt = 0;
  Left      : LongInt;
  Now       : LongInt;
Begin
  Write (#12 + #08 + #32 + #08 + Message + PadLT(Int2Str(TimeOut), 2, '0'));

  Repeat
    If KeyPressed Then Begin
      If ReadKey = #27 Then
        EscCount := EscCount + 1;
    End Else
      Delay(100);

    Now  := TimerMS;
    Left := ((TimeEnd - Now) / 1000) + 1;

    If Left <> Last Then Begin
      Write (#08 + #08 + PadLT(Int2Str(Left), 2, '0'));

      Last := Left;
    End;

  Until (Now > TimeEnd) or (EscCount > 1);

  If EscCount < 2 Then
    MenuCmd('GI', '');
End.