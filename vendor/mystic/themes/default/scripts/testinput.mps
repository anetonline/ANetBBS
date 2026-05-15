// =========================================================================
// TESTINPUT : MPL example of using the ANSI input and box classes
// =========================================================================

Var
  Box   : LongInt;
  In    : LongInt;
  InPos : Byte    = 1;
  Str   : String  = 'Input default';
  Str2  : String  = '';
  Num   : LongInt = 1;
Begin
  PurgeInput;

  ClassCreate (Box, 'box');
  ClassCreate (In, 'input');

  BoxHeader (Box, 0, 31, ' Input Demo ');

  InputOptions (In,          // Input class handle
                31,          // Attribute of inputted text
                25,          // Attribute to use for field input filler
                #176,        // Character to use for field input filler
                #9,          // Input will exit on these "low" ascii characters
                             // TAB
                #72 + #80,   // Exit on these extended characters
                '*');        // Password input echo character

  BoxOpen (Box, 20, 5, 60, 12);

  Repeat
    WriteXY (22,  7, 112, 'String Input > ' + PadRT(Str, 22, ' '));
    WriteXY (22,  8, 112, 'Number Input > ' + PadRT(Int2Str(Num), 5, ' '));
    WriteXY (22,  9, 112, 'Password     > ' + PadRT(strRep('*', Length(Str2)), 22, ' '));
    WriteXY (37, 11, 112, ' DONE ');

    Case InPos of
      1 : Str  := InputString (In, 37, 7, 22, 22, 1, Str);
      2 : Num  := InputNumber (In, 37, 8, 5, 5, 1, 65000, Num);
      3 : Str2 := InputString (In, 37, 9, 22, 22, 4, Str2);
      4 : If InputEnter (In, 37, 11, 6, ' DONE ') Then Break;
    End;

    Case InputExit(In) of
      #09,
      #80 : If InPos < 4 Then InPos := InPos + 1 Else InPos := 1;
      #72 : If InPos > 1 Then InPos := InPos - 1 Else InPos := 4;
    End;
  Until False;

  BoxClose  (Box);
  ClassFree (Box);
  ClassFree (In);
End.
