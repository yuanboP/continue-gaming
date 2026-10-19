# Public quality inspection rule

Inspect each query circuit board against its independent normal reference of the same board type. Reject a board with a visible structural defect: bent or broken pins/connectors, a missing or damaged component, a cracked or broken board/part, or a conspicuous foreign material or damaging contamination. Normal manufacturing variation, printing, component markings, illumination, reflections and a different camera orientation are not defects by themselves.

The reference is a different physical normal example. Exact pixel differences or text differences alone do not establish a defect. Examine the whole query. Images preserve the complete original field of view; no supplied box or crop marks a defect.

The physical action is a timed reject at the downstream gate, using d/f/j/k for its four lanes. Accept normal items by leaving them alone. Do not use anonymous carrier IDs, SKU names, the order of appearance, or assumed class proportions as a quality signal. A correct quality judgment delivered after the gate has passed cannot reject the item.

The trusted verifier uses independent dataset annotations and the actual action times. This is a simulated inspection and rejection workflow, not a certified production quality system.
