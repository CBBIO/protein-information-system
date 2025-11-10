import logging
import argparse
import subprocess

from protein_information_system.helpers.services.services import check_services
import os
import sys

module_dir = os.path.abspath(os.path.dirname(__file__))
os.chdir(module_dir)
sys.path.insert(0, module_dir)


def main(config_path='config/config.yaml'):
    from protein_information_system.helpers.config.yaml import read_yaml_config
    conf = read_yaml_config(config_path)

    logger = logging.getLogger("protein_information_system")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Step 1: Import ORM-based logic & check model coherence
    from protein_information_system.sql.model.model import (
        AccessionManager,
        UniProtExtractor,
        PDBExtractor,
        SequenceEmbeddingManager,
        Structure3DiManager,
        GOAnnotationsQueueProcessor
    )

    # Step 2: Check services running
    check_services(conf, logger)

    # Step 3: Run components
    #GOAnnotationsQueueProcessor(conf).start()
    #AccessionManager(conf).fetch_accessions_from_api()
    #AccessionManager(conf).load_accessions_from_csv()
    #UniProtExtractor(conf).start()
    #PDBExtractor(conf).start()
    #SequenceEmbeddingManager(conf).start()
    #Structure3DiManager(conf).start()


def ensure_dump_directory(dump_path):
    """Create directory for dump file if it doesn't exist"""
    dump_dir = os.path.dirname(dump_path)
    
    if dump_dir and not os.path.exists(dump_dir):
        try:
            os.makedirs(dump_dir, exist_ok=True)
            print(f"📁 Created directory: {dump_dir}")
        except PermissionError:
            print(f"❌ Permission denied creating directory: {dump_dir}")
            return False
        except Exception as e:
            print(f"❌ Error creating directory {dump_dir}: {e}")
            return False
    
    return True


def create_dump(dump_name, force=False):
    """Create database dump with specified name"""
    
    # Ensure dump directory exists
    if not ensure_dump_directory(dump_name):
        return False
    
    # Check if file exists
    if os.path.exists(dump_name) and not force:
        response = input(f"⚠️  File '{dump_name}' already exists. Overwrite? [y/N]: ")
        if response.lower() not in ['y', 'yes']:
            print("❌ Dump creation cancelled.")
            return False
    
    print(f"🗄️ Creating database dump: {dump_name}")
    
    # Create dump using Docker
    cmd = [
        "sudo", "docker", "exec", "-it", "pgvectorsql", 
        "pg_dump", "-U", "usuario", "-d", "BioData", 
        "-F", "c", "-v", "-f", f"/tmp/{os.path.basename(dump_name)}"
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        # Copy dump out of container
        copy_result = subprocess.run([
            "sudo", "docker", "cp", 
            f"pgvectorsql:/tmp/{os.path.basename(dump_name)}", 
            dump_name
        ], capture_output=True, text=True)
        
        if copy_result.returncode == 0:
            file_size = os.path.getsize(dump_name) / (1024 * 1024)  # MB
            print(f"✅ Dump created successfully: {dump_name} ({file_size:.1f} MB)")
            return True
        else:
            print(f"❌ Failed to copy dump from container: {copy_result.stderr}")
            return False
    else:
        print(f"❌ Dump creation failed: {result.stderr}")
        return False


def restore_dump(dump_name):
    """Restore dump to test database"""
    
    if not os.path.exists(dump_name):
        print(f"❌ Dump file '{dump_name}' not found.")
        return False
    
    print(f"🔄 Restoring dump '{dump_name}' to test database...")
    
    # Drop and recreate test database
    drop_cmd = [
        "sudo", "docker", "exec", "-it", "pgvectorsql", 
        "psql", "-U", "usuario", "-d", "BioData", 
        "-c", "DROP DATABASE IF EXISTS biodata_test;"
    ]
    subprocess.run(drop_cmd)
    
    create_cmd = [
        "sudo", "docker", "exec", "-it", "pgvectorsql", 
        "psql", "-U", "usuario", "-d", "BioData", 
        "-c", "CREATE DATABASE biodata_test;"
    ]
    subprocess.run(create_cmd)
    
    # Copy and restore dump
    copy_result = subprocess.run([
        "sudo", "docker", "cp", 
        dump_name, 
        f"pgvectorsql:/tmp/{os.path.basename(dump_name)}"
    ], capture_output=True, text=True)
    
    if copy_result.returncode != 0:
        print(f"❌ Failed to copy dump to container: {copy_result.stderr}")
        return False
    
    restore_result = subprocess.run([
        "sudo", "docker", "exec", "-it", "pgvectorsql", 
        "pg_restore", "-U", "usuario", "-d", "biodata_test", 
        f"/tmp/{os.path.basename(dump_name)}"
    ], capture_output=True, text=True)
    
    if restore_result.returncode == 0:
        print("✅ Dump restored successfully to biodata_test")
        return True
    else:
        print(f"❌ Dump restoration failed: {restore_result.stderr}")
        return False


def verify_dump():
    """Verify dump integrity"""
    try:
        from protein_information_system.helpers.dump_verification import verify_dump_integrity
        return verify_dump_integrity()
    except ImportError as e:
        print(f"⚠️  Could not import dump verification: {e}")
        return False


def get_default_dump_name():
    """Generate default dump name with timestamp"""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"dumps/biodata_dump_{timestamp}.dump"


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Protein Information System')
    parser.add_argument('--config', default='config/config.yaml', help='Path to config file')
    parser.add_argument('--test', action='store_true', help='Use test database')
    
    # Dump management arguments
    parser.add_argument('--create-dump', metavar='DUMP_NAME', help='Create database dump with specified name')
    parser.add_argument('--restore-dump', metavar='DUMP_NAME', help='Restore dump from specified file')
    parser.add_argument('--verify-dump', action='store_true', help='Verify dump integrity')
    parser.add_argument('--force', action='store_true', help='Force overwrite existing dump file')
    
    # Full workflow
    parser.add_argument('--full-dump-test', metavar='DUMP_NAME', nargs='?', 
                       help='Create dump, restore, and verify (optional dump name)')
    
    args = parser.parse_args()
    
    if args.create_dump:
        create_dump(args.create_dump, force=args.force)
        
    elif args.restore_dump:
        restore_dump(args.restore_dump)
        
    elif args.verify_dump:
        verify_dump()
        
    elif args.full_dump_test is not None:
        dump_name = args.full_dump_test if args.full_dump_test else get_default_dump_name()
        print(f"🚀 Starting full dump test workflow with: {dump_name}")
        
        if create_dump(dump_name, force=args.force):
            if restore_dump(dump_name):
                verify_dump()
            else:
                print("❌ Full dump test failed during restoration")
        else:
            print("❌ Full dump test failed during dump creation")
            
    elif args.test:
        main('config/config_test.yaml')
        
    else:
        # Original behavior - run main only (no verification by default)
        main()