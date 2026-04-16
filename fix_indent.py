#!/usr/bin/env python3
"""Fix indentation in telegram.py"""

with open('freqtrade/rpc/telegram.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix lines 2974-3039 (try block content)
# These lines should be indented 4 more spaces (inside try block)
fixed_lines = []
for i, line in enumerate(lines, 1):
    if 2974 <= i <= 3039:
        # Check if line is already properly indented (12 spaces for try block)
        stripped = line.lstrip()
        if stripped and not line.startswith('            '):
            # Add proper indentation (12 spaces = function level + try block)
            if line.startswith('        '):  # 8 spaces (function level)
                fixed_lines.append('            ' + stripped)
            elif line.startswith('    '):  # 4 spaces
                fixed_lines.append('            ' + stripped)
            else:
                fixed_lines.append('            ' + line.lstrip() if stripped else line)
        else:
            fixed_lines.append(line)
    else:
        fixed_lines.append(line)

with open('freqtrade/rpc/telegram.py', 'w', encoding='utf-8') as f:
    f.writelines(fixed_lines)

print("Fixed indentation")

