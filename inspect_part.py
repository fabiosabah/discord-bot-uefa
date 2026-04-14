from google.genai import types
import inspect
print('SIG', inspect.signature(types.Part.from_uri))
print('DOC', types.Part.from_uri.__doc__)
