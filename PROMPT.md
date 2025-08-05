I have a very important task: read the documents in the "doc" directory, especially the markdown files, and build the domain knowledge base of this app, in english.

1) Create doc/dev/modules where you explain in detail the purpose of each Python implementation module, how it works, its implementation, and any other useful information for you to work better with the codebase in the future.

2) Only after doing this, using the context gained from studying the modules, carefully check that the semantic execution engine is documented, especially how tasks are queued, if there is a lazy data structure, dask, etc.

3) Only after doing this, with the context gained from studying the semantic execution engine, unify the development documents and those also related to development but scattered in parent directories and others, into the "dev" subdirectory of "doc" and make them consistent and uniform, placing them in a subdirectory "doc/dev/notes".

4) Create an index of the documentation in doc/dev/README.md to help both you and me navigate all the documentation