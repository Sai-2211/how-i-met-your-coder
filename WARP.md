# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

This is currently a minimal repository named "how-i-met-your-coder" containing only a basic README.md file. The repository appears to be a new project without established code structure, build systems, or development workflows yet.

## Current State

- **Project Name**: how-i-met-your-coder
- **Repository Size**: ~124KB (mostly git metadata)
- **Languages/Framework**: Not yet established
- **Files**: Only README.md with a simple title

## Development Workflow

Since this is a new/empty repository, there are no established development commands yet. Future instances of Warp should:

1. **Check for new files**: Run `ls -la` or `find . -type f | grep -v .git` to see what has been added since this WARP.md was created
2. **Identify the tech stack**: Look for common configuration files (package.json, requirements.txt, Cargo.toml, go.mod, etc.) to understand what technology stack is being used
3. **Discover build commands**: Once a tech stack is established, check for standard build/test/lint commands based on the framework being used

## Repository Structure

Currently minimal:
```
.
├── README.md      # Basic project title
└── WARP.md        # This file
```

## Git Information

- **Default Branch**: main
- **Remote**: origin (GitHub repository)
- **Current Status**: Clean working tree, up to date with origin

## Notes for Future Development

When this repository evolves:

- Update this WARP.md with specific build commands once a tech stack is chosen
- Add architecture documentation as the codebase grows
- Include any project-specific development practices or conventions that emerge
- Document any environment setup requirements