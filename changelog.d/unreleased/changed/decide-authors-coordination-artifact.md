- The decide coordinator now authors a third child alongside Decisions and
  Steps: an "Update plan and begin coordination" artifact Thing whose body
  references the `lot-coordinate-begin` skill plus the task's specifics. Its
  handoff tells the human to answer the Decisions and then launch execution
  with `lot claude send <model> <artifact-id>`.
