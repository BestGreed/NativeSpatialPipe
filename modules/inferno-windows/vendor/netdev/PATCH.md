This is netdev 0.32.0 from crates.io, with its original MIT license retained.

One Windows fix: `SendARP` can return NO_ERROR with an output length of zero
for an adapter without a usable neighbor MAC. Only assume-initialize and use
the six-byte output when its reported length is exactly six; otherwise return
the existing zero-MAC fallback. This prevents adapter enumeration from panicking
on the test machine before the requested Dante adapter can be selected.

No protocol or interface-selection behavior is otherwise changed.
