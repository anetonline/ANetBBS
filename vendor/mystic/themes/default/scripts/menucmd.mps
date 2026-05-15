// Very basic MPL program designed to execute a menu command from a prompt
// or from command line.

Var
  Cmd  : String[2];
  Data : String;
Begin
  Cmd  := Upper(Copy(ProgParams, 1, 2));
  Data := Copy(ProgParams, 4, Length(ProgParams));

  MenuCmd(Cmd, Data);
End.
