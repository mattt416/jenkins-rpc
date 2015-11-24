# find rsyslog container path
MASTER_OPENSTACK_DIR='/openstack'
CONTAINER_DIRS=$(ls $MASTER_OPENSTACK_DIR)

for CONTAINER_DIR in $CONTAINER_DIRS; do
    if [[ $CONTAINER_DIR == *"rsyslog"* ]]; then
        RSYSLOG_CONTAINER_DIR=$CONTAINER_DIR
    fi
done

# set master log path
LOG_MASTER_PATH="/openstack/$RSYSLOG_CONTAINER_DIR/log-storage"

# Move into the rsyslog path and remove .gz files
cd $LOG_MASTER_PATH
LOG_DIRS=$(for i in `ls`;do echo $i;done 2>&1)
for d in $LOG_DIRS; do
    pushd $d
    FILES=$(ls | grep -e .gz)
    for f in $FILES; do
        echo "Deleting $f"
        rm -f $f
    done
    popd
done
