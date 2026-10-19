# Quality rule

Compare the entire query circuit board with its independent normal reference of the same board type. Reject a visible structural defect: bent or broken pins or connectors, a missing or damaged component, a cracked or broken board or part, conspicuous foreign material, or damaging contamination.

Normal manufacturing variation, printing, component markings, illumination, reflections, and camera orientation are not defects by themselves. The reference is a different physical normal example. Exact pixel or text differences alone do not establish a defect. No supplied box, crop, mask, or label identifies a defective region.

Use the corresponding `d/f/j/k` key to reject a board at its lane's downstream gate. Accept a normal item by leaving it alone. Anonymous IDs, SKU aliases, ordering, and assumed class proportions are not quality signals. A correct diagnosis delivered after the gate has passed cannot reject that board.

The independent verifier uses dataset annotations and actual action times. This is a simulated visual inspection and timed separation task, not a certified production system.
