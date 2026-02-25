# Database Schema Organization

This directory contains the PostgreSQL database schema files for ElRipley, organized by domain and purpose.

## File Structure

### Setup Files
- **`00_init_agent_role.sql`** - Agent user role setup
  - Creates `agent_user` role for AI agent SQL queries
  - Grants basic permissions (CONNECT, USAGE)
  - Password set from environment variable

### Schema Files (Domain-Specific)
Schema files are numbered and organized by business domain:

- **`01_schema_users.sql`** - User management domain
  - Users, roles, refresh tokens
  - User files and storage usage
  - User API keys (BYOK support)

- **`02_schema_facebook.sql`** - Facebook integration domain
  - Facebook app scope users, fan pages, page admins
  - Posts, comments, reactions
  - Conversation messages and comments
  - Sync states and media assets

- **`03_schema_openai.sql`** - OpenAI integration domain
  - OpenAI conversations, messages, branches
  - OpenAI responses and agent responses
  - Branch message mappings

- **`04_schema_suggest_response.sql`** - Suggest response domain
  - Page and user-level prompts
  - Suggest response history

- **`05_schema_media.sql`** - Media assets domain
  - Unified media assets table (user uploads + Facebook mirrors)
  - User storage quotas
  - Prompt media relationships

### Indexes File
- **`indexes.sql`** - All database indexes
  - Created after all table definitions
  - Contains indexes for all domains
  - Organized by domain sections

### Security Files
- **`99_rls_policies.sql`** - Row-Level Security (RLS) policies
  - Enables RLS on Facebook-related tables
  - Creates policies for `agent_user` role
  - Filters data based on user's accessible pages
  - See `docs/AGENT_SQL_RLS_SETUP.md` for details

### Main Schema File
- **`elripley.sql`** - Main entry point
  - Imports `00_init_agent_role.sql` first (role setup)
  - Imports all domain schema files in order
  - Imports indexes file
  - Grants SELECT permissions to `agent_user`
  - Imports `99_rls_policies.sql` last (RLS setup)
  - Contains initial data (default roles)
  - Contains documentation and relationship notes

### Documentation Files
- **`elripley.dbml`** - Database schema diagram (DBML format)
  - Visual representation of all tables and relationships
  - Used for generating ER diagrams

## Import Order

The schema files must be imported in this order:

1. `00_init_agent_role.sql` - Create agent_user role (before tables)
2. `01_schema_users.sql` - Base user tables
3. `02_schema_facebook.sql` - Facebook tables (may reference users)
4. `03_schema_openai.sql` - OpenAI tables (may reference users)
5. `04_schema_suggest_response.sql` - Suggest response tables (references users, fan_pages, agent_response)
6. `05_schema_media.sql` - Media assets tables (references users)
7. `indexes.sql` - All indexes (must be after all tables)
8. Grant SELECT to agent_user (after tables exist)
9. `99_rls_policies.sql` - RLS policies (after all tables and grants)

## Adding New Schema Files

When adding a new domain schema file:

1. **Number the file**: Use the next available number (e.g., `05_schema_newdomain.sql`)
2. **Add to elripley.sql**: Add the import statement in the correct order
3. **Add indexes**: Add any indexes to `indexes.sql` in the appropriate section
4. **Update dbml**: Add tables and relationships to `elripley.dbml`
5. **Update this README**: Document the new domain

## Migration Files

Migration files are stored in `../migrations/` directory and are used to modify existing databases. They should:
- Be numbered sequentially (e.g., `001_add_messages_metadata.sql`)
- Be idempotent (use `IF NOT EXISTS` or check before applying)
- Include backfill logic for existing data when needed

Current migrations:
- **`001_add_messages_metadata.sql`** – Add `metadata JSONB` to `messages` (AI vs admin tagging). See `docs/FE_MESSAGE_METADATA.md` for FE usage.
- **`002_add_comments_metadata.sql`** – Add `metadata JSONB` to `comments` (AI vs admin tagging + instant reply). See `docs/FE_COMMENT_METADATA.md` for FE usage.
- **`003_page_admin_suggest_config_agent_write.sql`** – Grant `agent_writer` INSERT and UPDATE on `page_admin_suggest_config`; add RLS policies so general agent can create/update (not delete) per-page suggest config.

## Best Practices

1. **Separation of Concerns**: Each domain has its own schema file
2. **Indexes Centralized**: All indexes in one file for easier maintenance
3. **Documentation**: Keep `elripley.sql` and `elripley.dbml` updated
4. **Naming Conventions**: 
   - Schema files: `NN_schema_domain.sql`
   - Indexes file: `indexes.sql` (no number prefix to avoid conflicts)
   - Migration files: `NNN_description.sql`

