# Changelog

This changelog starts at version 2.0.0.  
Previous history is not included.

## [2.0.0] - 2025-07-28

### Added
- Full support for the **ESM3c** model for protein embeddings.
- Advanced task configuration options, including `batch_size`, model-specific activation.
- Integration of **Poetry** into the Docker workflow for improved environment reproducibility.
- `.dockerignore` file to optimize Docker image builds.

### Changed
- Embedding logic has been significantly improved:
  - **Removed forced truncation to 512 residues** across all models.
  - **Excluded special tokens** (e.g. CLS, PAD) from final residue embeddings, ensuring cleaner and biologically relevant representations.
  - Revised mean computation logic to reflect these changes.
- Unified parameter naming, such as `task_name` for ESM-based tasks, for better consistency.
- Refactored log directory handling: more robust directory creation and user path resolution.
- Minor formatting and whitespace improvements throughout the code.

### Removed
- Obsolete files.

### Fixed
- Improved stability in RabbitMQ connection handling to better tolerate failures.
